"""The redeem-failure copy is the only thing between a user and silence.

Today an offer code that does not work produces NOTHING: `redeemOfferCode`
is a bare `UIApplication.open` with no callback, success arrives via
`Transaction.updates`, and failure is silent. SS built the client half and it
deliberately presents nothing until GP serves this block, with no English
fallback on a subscription surface. So a missing or malformed key here does
not degrade the experience, it restores the silence.

Locale parity is already pinned structurally by test_locale_parity. What is
pinned HERE is the things parity cannot see: that the payloads carry real
text rather than placeholders, that the action allowlist holds, that the
numbers are identical across locales, and that the copy obeys the two
standing rules it would otherwise quietly break.
"""

import json
import pathlib

import pytest

REMOTE = pathlib.Path("config/remote")
LOCALES = ("en", "es", "fr", "ja")
PAYLOADS = (
    "promo_redeem_unconfirmed",
    "promo_redeem_settling",
    "promo_redeem_settling_signed_out",
)
# GP owns this allowlist. `contact_support` has no destination and `sign_in`
# needs client work that is not in v1, so the text carries it instead.
ALLOWED_ACTIONS = {"retry_redeem"}


def _cfg(loc: str) -> dict:
    name = "client-config.json" if loc == "en" else f"client-config.{loc}.json"
    return json.loads((REMOTE / name).read_text())


def _redeem(loc: str) -> dict:
    return _cfg(loc)["redeem"]


def _strings(block: dict) -> list[str]:
    out = []
    for key, payload in block.items():
        if key.startswith("_") or not isinstance(payload, dict):
            continue
        for k, v in payload.items():
            if isinstance(v, str) and k != "secondary_action":
                out.append(v)
    return out


@pytest.mark.parametrize("loc", LOCALES)
def test_every_locale_carries_every_payload(loc):
    block = _redeem(loc)
    for name in PAYLOADS:
        assert name in block, f"{loc}: {name} missing"
        for field in ("title", "body", "dismiss_label"):
            assert block[name].get(field), f"{loc}/{name}: {field} empty"


@pytest.mark.parametrize("loc", LOCALES)
def test_only_the_unconfirmed_payload_offers_a_secondary_action(loc):
    """Retry is only honest where a retry could help. Settling has nothing
    for the user to do, and the signed-out case needs a sign-in the client
    cannot yet perform, so offering a button there would be a dead end."""
    block = _redeem(loc)
    assert block["promo_redeem_unconfirmed"]["secondary_action"] == "retry_redeem"
    assert block["promo_redeem_unconfirmed"].get("secondary_label")
    for name in ("promo_redeem_settling", "promo_redeem_settling_signed_out"):
        assert "secondary_action" not in block[name], \
            f"{loc}/{name} offers an action the client cannot fulfil"


@pytest.mark.parametrize("loc", LOCALES)
def test_secondary_action_is_inside_the_allowlist(loc):
    for name, payload in _redeem(loc).items():
        if not isinstance(payload, dict):
            continue
        action = payload.get("secondary_action")
        if action is not None:
            assert action in ALLOWED_ACTIONS, \
                f"{loc}/{name}: {action!r} is not a GP-served action"


def test_numbers_are_identical_across_locales():
    """A localised file may change words, never a gate or a timer. The same
    rule the locale-parity test enforces on shape, applied to values."""
    base = _redeem("en")["redeem_timeout_ms"]
    assert base == 8000
    for loc in LOCALES:
        assert _redeem(loc)["redeem_timeout_ms"] == base, \
            f"{loc} changed a timer in a translation"


@pytest.mark.parametrize("loc", LOCALES)
def test_no_dash_punctuation_in_served_copy(loc):
    """Standing rule, and it applies to served strings, not just prose. A
    dash in shipped copy is also a dash the model sees if this text ever
    lands in a prompt."""
    for s in _strings(_redeem(loc)):
        assert "—" not in s, f"{loc}: em dash in {s!r}"
        assert "–" not in s, f"{loc}: en dash in {s!r}"


@pytest.mark.parametrize("loc", LOCALES)
def test_settling_copy_promises_no_duration(loc):
    """Honest-progress rule. We do not know how long settling takes, so any
    'a few minutes' would be a number nobody measured. Pinned because it is
    the single most tempting edit to this block."""
    body = _redeem(loc)["promo_redeem_settling"]["body"]
    for claim in ("minute", "minuto", "seconde", "segundo", "second",
                  "hour", "hora", "heure", "分", "秒", "時間"):
        assert claim not in body.lower(), \
            f"{loc}: settling copy claims a duration ({claim!r})"


@pytest.mark.parametrize("loc", LOCALES)
def test_the_translations_are_actually_translated(loc):
    """The failure this catches is a locale file that silently carries the
    English string, which parity cannot see because the KEY is present. The
    dismiss label is excluded: 'OK' is legitimately 'OK' in Japanese."""
    if loc == "en":
        pytest.skip("reference locale")
    en, other = _redeem("en"), _redeem(loc)
    for name in PAYLOADS:
        for field in ("title", "body"):
            assert other[name][field] != en[name][field], \
                f"{loc}/{name}/{field} is still the English string"
