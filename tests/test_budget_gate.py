"""Pure-function tests for the budget-gate logic. Integration tests for
the full /v1/chat and /v1/reports paths live in tests/integration/.
"""

from unittest.mock import MagicMock

from app.services.budget_gate import (
    CREDITS_PER_DOLLAR,
    OVERAGE_TOLERANCE_USD,
    dollars_to_credits,
    estimate_call_cost_usd,
    estimate_input_tokens,
    would_exceed_budget,
)


class TestCreditsConversion:
    def test_one_dollar_is_ten_thousand_credits(self):
        """1 cent = 100 credits → 1 USD = 10,000 credits. If this constant
        changes, every CTA copy that mentions a number changes too — pin it."""
        assert CREDITS_PER_DOLLAR == 10_000
        assert dollars_to_credits(1.00) == 10_000

    def test_free_tier_budget_in_credits(self):
        """Free's $0.35 cap → 3,500 credits. The whole 'sounds impressive'
        framing depends on this. Pin it explicitly."""
        assert dollars_to_credits(0.35) == 3_500

    def test_overage_tolerance_in_credits(self):
        """$0.05 overage → 500 credits."""
        assert dollars_to_credits(OVERAGE_TOLERANCE_USD) == 500

    def test_rounds_to_nearest(self):
        # 0.0001 USD = 1 credit (exactly)
        assert dollars_to_credits(0.0001) == 1
        # Sub-credit rounds half-to-even (Python's default)
        assert dollars_to_credits(0.00005) == 0


class TestInputTokenEstimate:
    def test_matches_ss_chars_div_4(self):
        """SS computes (text.count + 3) / 4 (Swift integer division).
        Must match exactly so the gauge and gate agree."""
        # Empty string
        assert estimate_input_tokens("") == 0
        # Boundary: 4 chars = 1 token
        assert estimate_input_tokens("abcd") == 1
        # 5 chars = (5+3)/4 = 2 tokens (matches Swift int math)
        assert estimate_input_tokens("abcde") == 2
        # 100 chars = (100+3)/4 = 25 tokens
        assert estimate_input_tokens("a" * 100) == 25

    def test_unicode_counts_codepoints(self):
        """Swift's text.count counts grapheme clusters; Python's len() counts
        codepoints. Close enough for a soft cap, but a heavy emoji/CJK prompt
        will diverge slightly. Documenting the divergence so future-us
        doesn't chase a phantom bug."""
        # Single emoji is 1 codepoint in Python but ~1 grapheme cluster in Swift
        assert estimate_input_tokens("✨") == 1
        # CJK is 1 codepoint per char both sides
        assert estimate_input_tokens("こんにちは") == 2  # (5+3)//4


class TestBudgetExceeded:
    def test_unlimited_never_exceeds(self):
        """Plus/Pro/Admin run with effective_limit_usd=-1 — gate must
        always return False or they'd be blanket-blocked."""
        assert not would_exceed_budget(0.0, 100.0, -1)
        assert not would_exceed_budget(99999.0, 99999.0, -1)

    def test_well_under_budget(self):
        # Free: $0.10 used + $0.001 estimated, $0.35 limit → easily under.
        assert not would_exceed_budget(0.10, 0.001, 0.35)

    def test_just_under_budget(self):
        # $0.30 used + $0.05 estimated = $0.35 = limit. With $0.05 tolerance,
        # ceiling is $0.40. So 0.35 ≤ 0.40 → allowed.
        assert not would_exceed_budget(0.30, 0.05, 0.35)

    def test_within_tolerance(self):
        # $0.34 used + $0.05 estimated = $0.39, below $0.35+$0.05=$0.40 ceiling.
        assert not would_exceed_budget(0.34, 0.05, 0.35)

    def test_just_over_tolerance(self):
        # $0.35 used + $0.06 estimated = $0.41, above $0.40 ceiling.
        assert would_exceed_budget(0.35, 0.06, 0.35)

    def test_already_well_over(self):
        # $0.50 used (somehow) + any estimate → over.
        assert would_exceed_budget(0.50, 0.001, 0.35)


class TestCallCostEstimate:
    def _mock_pricing(self, input_per_token: float, output_per_token: float):
        m = MagicMock()
        m.get_model_pricing.return_value = {
            "input_cost_per_token": input_per_token,
            "output_cost_per_token": output_per_token,
        }
        return m

    def test_uses_input_plus_max_output(self):
        # Haiku-ish pricing: $0.25/M input, $1.25/M output
        p = self._mock_pricing(0.25e-6, 1.25e-6)
        cost = estimate_call_cost_usd(p, "anthropic", "haiku", input_tokens=1000, max_output_tokens=500)
        # 1000 * 0.25e-6 + 500 * 1.25e-6 = 0.00025 + 0.000625 = 0.000875
        assert cost is not None
        assert abs(cost - 0.000875) < 1e-9

    def test_falls_back_to_default_max_output(self):
        """When max_output_tokens is None, use DEFAULT_MAX_OUTPUT_TOKENS=4096
        — worst case so we never under-estimate."""
        p = self._mock_pricing(0.25e-6, 1.25e-6)
        cost_none = estimate_call_cost_usd(p, "x", "y", input_tokens=0, max_output_tokens=None)
        cost_4096 = estimate_call_cost_usd(p, "x", "y", input_tokens=0, max_output_tokens=4096)
        assert cost_none == cost_4096

    def test_returns_none_when_pricing_missing(self):
        """Fail open: a transient pricing-data outage shouldn't blanket-block
        users. Caller treats None as 'skip the gate'."""
        m = MagicMock()
        m.get_model_pricing.return_value = None
        cost = estimate_call_cost_usd(m, "x", "y", input_tokens=1000, max_output_tokens=500)
        assert cost is None

    def test_returns_none_when_pricing_zero(self):
        """A model entry with zero prices (degenerate upstream data) should
        also fail open — we can't tell if it's actually free or just missing."""
        p = self._mock_pricing(0.0, 0.0)
        cost = estimate_call_cost_usd(p, "x", "y", input_tokens=1000, max_output_tokens=500)
        assert cost is None
