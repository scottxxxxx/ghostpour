"""Served tier copy must quantify the free allowance in meeting-hours.

Scott's call 2026-07-29: a credit balance carries no scale, so it cannot
inform a purchase decision ("1200 credits" could be twelve seconds or twelve
years). Hours are approximate but meaningful, and they are also more accurate
than what we were shipping.

Measured against production, free-tier usage runs about $0.08 per
meeting-hour, so the free monthly budget covers roughly twelve hours of
observed behavior. "About five" is deliberately conservative, leaves margin
for heavier questioning, and matches the hours_per_month we already serve.

Three surfaces described the free tier three different ways before this:
the website said one hour (and framed a recurring allowance as a one-time
trial), the served copy said an unquantified credit allowance, and the config
field said five hours.
"""

import json
from pathlib import Path

LOCALES = ("tiers.json", "tiers.es.json", "tiers.fr.json", "tiers.ja.json")
BANNED = ("credit", "crédit", "credito", "クレジット")
NUMERALS = ("five", "cinco", "cinq", "5")


def test_free_tier_copy_is_anchored_in_hours_not_credits():
    """Served free-tier copy must quantify in meeting-hours, never credits.

    Scott's call 2026-07-29: a credit balance carries no scale, so it cannot
    inform a purchase decision ("1200 credits" could be twelve seconds or
    twelve years). Hours are approximate but meaningful. Measured against
    production, free-tier usage runs ~$0.08 per meeting-hour, so the monthly
    budget covers roughly twelve hours of observed behavior; "about five"
    is deliberately conservative and matches the hours_per_month we serve.
    """
    for name in LOCALES:
        blob = Path("config/remote") / name
        raw = blob.read_text().lower()
        for banned in BANNED:
            assert banned not in raw, f"{name} still sells the free tier in {banned!r}"
        free = json.loads(blob.read_text())["tiers"]["free"]
        # the first bullet is the allowance line, and it has to carry a number
        bullet = free["feature_bullets"][0]
        assert free["feature_items"][0]["label"] == bullet, (
            f"{name}: bullet and feature_item must stay in sync"
        )
        assert any(t in bullet for t in NUMERALS), (
            f"{name}: allowance bullet must name the hours: {bullet!r}"
        )
