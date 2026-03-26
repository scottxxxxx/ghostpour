"""Pricing service that fetches and caches LLM model costs from an external source.

Default source: LiteLLM's model_prices_and_context_window.json
Users can override via CZ_PRICING_SOURCE_URL (legacy prefix) to point at any compatible JSON.

The JSON format expected is a dict keyed by model ID, where each value contains:
  - input_cost_per_token (float, USD)
  - output_cost_per_token (float, USD)
  - cache_read_input_token_cost (float, USD, optional)
  - cache_creation_input_token_cost (float, USD, optional)
  - output_cost_per_reasoning_token (float, USD, optional)
"""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger("ghostpour.pricing")

DEFAULT_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# How often to refresh pricing data (seconds)
DEFAULT_REFRESH_INTERVAL = 86400  # 24 hours


class PricingService:
    def __init__(
        self,
        source_url: str = DEFAULT_PRICING_URL,
        refresh_interval: int = DEFAULT_REFRESH_INTERVAL,
    ):
        self.source_url = source_url
        self.refresh_interval = refresh_interval
        self._prices: dict = {}
        self._last_fetch: float = 0
        self._refresh_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Fetch pricing data and start background refresh."""
        await self._fetch()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    # Max reasonable price per token (USD). Anything above this is bad data.
    # $100/M tokens = $0.0001 per token. W&B entries have $0.071/token = $71,000/M.
    MAX_COST_PER_TOKEN = 0.0001  # $100/M tokens

    async def _fetch(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self.source_url)
                resp.raise_for_status()
                raw_data = resp.json()

                # Filter out entries with absurd pricing (bad upstream data)
                filtered = {}
                rejected = 0
                for model_id, info in raw_data.items():
                    inp = info.get("input_cost_per_token") or 0
                    out = info.get("output_cost_per_token") or 0
                    if inp > self.MAX_COST_PER_TOKEN or out > self.MAX_COST_PER_TOKEN:
                        rejected += 1
                        continue
                    filtered[model_id] = info

                self._prices = filtered
                self._last_fetch = time.monotonic()
                logger.info(
                    "Loaded pricing data: %d models from %s (%d rejected for bad pricing)",
                    len(self._prices),
                    self.source_url,
                    rejected,
                )
        except Exception as e:
            logger.warning("Failed to fetch pricing data: %s", e)
            # Keep stale data if we have it

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_interval)
            await self._fetch()

    def get_model_pricing(self, provider: str, model: str) -> dict | None:
        """Look up pricing for a model.

        Tries multiple key formats since LiteLLM uses various conventions:
          - "provider/model" (e.g., "openai/gpt-5.2")
          - "model" (e.g., "gpt-5.2")
          - "provider/model-id" with provider aliases
        """
        # LiteLLM uses provider prefixes like "openai/", "anthropic/", etc.
        provider_aliases = {
            "google": "gemini",
            "xai": "xai",
            "deepseek": "deepseek",
            "kimi": "kimi",  # May not exist in LiteLLM
            "qwen": "qwen",  # May not exist in LiteLLM
        }
        provider_key = provider_aliases.get(provider, provider)

        # Try various key formats
        candidates = [
            f"{provider_key}/{model}",
            model,
            f"{provider}/{model}",
        ]

        for key in candidates:
            if key in self._prices:
                return self._prices[key]

        return None

    def calculate_cost(
        self,
        provider: str,
        model: str,
        usage: dict | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> dict:
        """Calculate cost breakdown from usage data.

        Returns a dict with:
          - input_cost: float (USD)
          - output_cost: float (USD)
          - cached_savings: float (USD, how much was saved by caching)
          - total_cost: float (USD)
          - billable_input_tokens: int
          - billable_output_tokens: int
          - pricing_available: bool
        """
        result = {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "cached_savings": 0.0,
            "total_cost": 0.0,
            "billable_input_tokens": input_tokens or 0,
            "billable_output_tokens": output_tokens or 0,
            "pricing_available": False,
        }

        pricing = self.get_model_pricing(provider, model)
        if not pricing:
            return result

        result["pricing_available"] = True
        usage = usage or {}

        input_cost_per_token = pricing.get("input_cost_per_token", 0)
        output_cost_per_token = pricing.get("output_cost_per_token", 0)
        cache_read_cost = pricing.get("cache_read_input_token_cost", 0)
        cache_creation_cost = pricing.get("cache_creation_input_token_cost", 0)
        reasoning_cost = pricing.get("output_cost_per_reasoning_token", 0)

        raw_input = input_tokens or 0
        raw_output = output_tokens or 0

        # --- Cached token handling (provider-specific field names) ---

        # OpenAI: cached tokens are included in prompt_tokens,
        # reported in prompt_tokens_details.cached_tokens
        cached_input = (
            usage.get("prompt_tokens_details.cached_tokens")  # OpenAI (flattened)
            or usage.get("cache_read_input_tokens")  # Anthropic
            or usage.get("cachedContentTokenCount")  # Gemini
            or 0
        )

        cache_creation = usage.get("cache_creation_input_tokens", 0)  # Anthropic

        # Reasoning tokens (OpenAI o-series, DeepSeek reasoner)
        reasoning_tokens = (
            usage.get("completion_tokens_details.reasoning_tokens")  # OpenAI (flattened)
            or 0
        )

        # --- Billable token calculation ---

        # For OpenAI: cached tokens are IN prompt_tokens, charged at cache rate
        # For Anthropic: cache_read is separate from input_tokens
        # Safe approach: subtract cached from input for billable count
        billable_input = max(0, raw_input - cached_input)
        billable_output = raw_output

        result["billable_input_tokens"] = billable_input
        result["billable_output_tokens"] = billable_output

        # --- Cost calculation ---

        input_cost = billable_input * input_cost_per_token
        cached_cost = cached_input * (cache_read_cost or input_cost_per_token)
        creation_cost = cache_creation * (cache_creation_cost or input_cost_per_token)

        if reasoning_tokens and reasoning_cost:
            regular_output = max(0, raw_output - reasoning_tokens)
            output_cost = (
                regular_output * output_cost_per_token
                + reasoning_tokens * reasoning_cost
            )
        else:
            output_cost = raw_output * output_cost_per_token

        total_input_cost = input_cost + cached_cost + creation_cost
        full_price_input = raw_input * input_cost_per_token
        cached_savings = full_price_input - total_input_cost

        result["input_cost"] = round(total_input_cost, 8)
        result["output_cost"] = round(output_cost, 8)
        result["cached_savings"] = round(max(0, cached_savings), 8)
        result["total_cost"] = round(total_input_cost + output_cost, 8)

        return result

    @property
    def is_loaded(self) -> bool:
        return len(self._prices) > 0

    @property
    def model_count(self) -> int:
        return len(self._prices)
