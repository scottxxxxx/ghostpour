import yaml
from fastapi import HTTPException

from app.config import Settings
from app.models.chat import ChatRequest, ChatResponse
from app.services.providers.anthropic import AnthropicAdapter
from app.services.providers.base import ProviderAdapter
from app.services.providers.gemini import GeminiAdapter
from app.services.providers.generic import GenericAdapter
from app.services.providers.openai_compat import OpenAICompatAdapter

ADAPTER_MAP: dict[str, type[ProviderAdapter]] = {
    "openai": OpenAICompatAdapter,
    "anthropic": AnthropicAdapter,
    "gemini": GeminiAdapter,
}


class ProviderRouter:
    def __init__(self, provider_config_path: str, settings: Settings):
        with open(provider_config_path) as f:
            self._config = yaml.safe_load(f)["providers"]
        self._settings = settings
        self._adapters: dict[str, ProviderAdapter] = {}

    def _get_adapter(self, provider: str) -> ProviderAdapter:
        if provider in self._adapters:
            return self._adapters[provider]

        cfg = self._config.get(provider)
        if not cfg:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_request",
                    "message": f"Unknown provider: {provider}",
                },
            )

        api_key = getattr(self._settings, cfg["env_key"], "")
        if not api_key:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "provider_error",
                    "message": f"Provider '{provider}' is not configured (missing API key)",
                },
            )

        api_format = cfg["api_format"]

        if api_format == "generic":
            # Config-driven adapter — no code changes needed to add providers
            adapter = GenericAdapter(
                api_key=api_key,
                base_url=cfg["base_url"],
                auth_header=cfg["auth_header"],
                auth_prefix=cfg["auth_prefix"],
                extra_headers=cfg.get("extra_headers"),
                request_format=cfg.get("request_format"),
                response_mappings=cfg.get("response_mappings"),
                usage_paths=cfg.get("usage_paths"),
            )
        else:
            adapter_cls = ADAPTER_MAP.get(api_format)
            if not adapter_cls:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "provider_error",
                        "message": (
                            f"Unsupported api_format: '{api_format}'. "
                            f"Use 'openai', 'anthropic', 'gemini', or 'generic'."
                        ),
                    },
                )

            adapter = adapter_cls(
                api_key=api_key,
                base_url=cfg["base_url"],
                auth_header=cfg["auth_header"],
                auth_prefix=cfg["auth_prefix"],
                extra_headers=cfg.get("extra_headers"),
            )

        self._adapters[provider] = adapter
        return adapter

    def validate_model(self, provider: str, model: str) -> None:
        """Check that the requested model exists in the provider config."""
        cfg = self._config.get(provider, {})
        models = cfg.get("models", [])

        # If no models listed, allow any (open-ended provider)
        if not models:
            return

        model_ids = [m["id"] for m in models]
        if model not in model_ids:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_request",
                    "message": (
                        f"Model '{model}' not found for provider '{provider}'. "
                        f"Available: {model_ids}"
                    ),
                },
            )

    async def route(self, request: ChatRequest) -> ChatResponse:
        self.validate_model(request.provider, request.model)
        adapter = self._get_adapter(request.provider)
        return await adapter.send_request(request)

    async def close(self) -> None:
        """Close all adapter HTTP clients. Called on app shutdown."""
        for adapter in self._adapters.values():
            await adapter.close()
