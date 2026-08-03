"""Served configs must not brick a frozen build (2026-08-03).

SS's four rules, enforced rather than agreed. Every build in the field
decodes our payloads strictly and whole-file, all of their fail-soft work
postdates 2026-08-02, and so does `config_decode_failed`. So an older
build can neither tolerate a shape change nor tell us it failed. The
People incident was not a near miss, it was the general case arriving
once.

The rule this file enforces is the second: **every entry in a collection
carries every key its siblings carry**, even when semantically empty. One
entry missing a key the client's type declares non-optional throws, and
the throw takes the whole file, not the entry.

We cannot read their types, so "a key its siblings carry" is the
conservative proxy for "a key the type declares". Where a key is
genuinely optional the exemption is written down with its reason, so it
is a decision on the record rather than a silence. SS has offered a
machine-readable manifest of required keys per shipped build; when that
lands it replaces the exemptions below with facts.

Rules 1, 3 and 4 (additive only, never narrow a type, quiet is no signal
for pre-08-02 builds) are process rather than shape and live in
docs/decisions/prompt-composition-doctrine.md.
"""

import json
from pathlib import Path

import pytest

_REMOTE = Path(__file__).parent.parent / "config" / "remote"

# (config slug, dotted path to the collection). Collections of same-shaped
# entries, where one odd entry takes the file down with it.
COLLECTIONS = [
    ("model-capabilities", "models"),
    ("tiers", "feature_definitions"),
    ("tiers", "tiers"),
    ("entitlements", "matrix"),
]

LOCALES = ["", ".es", ".ja", ".fr"]

# Keys a sibling carries that another may legitimately omit. Each needs a
# reason, and the reason has to be about the client's type rather than
# about our data being tidy.
OPTIONAL_KEYS = {
    ("model-capabilities", "models"): {
        "reasoningLevels": (
            "Absent exactly on the six models with supportsReasoning=false. "
            "The file decodes on every shipped build today with it missing, "
            "which is the evidence that the type declares it optional."
        ),
    },
    ("tiers", "feature_definitions"): {
        "cta_strings": "Only features with quota CTAs carry it; shipped builds decode without it.",
        "free_quota_per_month": "Carried only by features with a free monthly quota, project_chat today.",
        "gp_chat_flag": "Routing flag that only the project_chat feature has ever carried.",
        "teaser_response": "The canned upsell body, carried only by features with a teaser state.",
        "signed_out": "people only; added 2026-08-03 and no shipped build requires it.",
    },
}


def _load(slug: str, loc: str):
    path = _REMOTE / f"{slug}{loc}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _dig(doc, dotted):
    for part in dotted.split("."):
        doc = doc[part]
    return doc


@pytest.mark.parametrize("slug,path", COLLECTIONS)
@pytest.mark.parametrize("loc", LOCALES)
def test_every_entry_carries_every_sibling_key(slug, path, loc):
    doc = _load(slug, loc)
    if doc is None:
        pytest.skip(f"{slug}{loc} not shipped")
    coll = _dig(doc, path)
    entries = {k: v for k, v in coll.items() if isinstance(v, dict)}
    if len(entries) < 2:
        pytest.skip("not a collection")

    union = set().union(*(set(v) for v in entries.values()))
    exempt = set(OPTIONAL_KEYS.get((slug, path), {}))
    required = union - exempt

    problems = {
        name: sorted(required - set(entry))
        for name, entry in entries.items()
        if required - set(entry)
    }
    assert not problems, (
        f"{slug}{loc}.{path}: {problems}. A key a sibling carries is one the "
        "client's type may declare non-optional, and a missing one discards "
        "the WHOLE file on every frozen build. Add the key (empty is fine) "
        "or exempt it in OPTIONAL_KEYS with a reason.")


@pytest.mark.parametrize("slug,path", COLLECTIONS)
def test_exemptions_are_justified_and_still_needed(slug, path):
    """An exemption with no reason is a silence with extra steps, and one
    that no longer applies is a hole nobody is watching."""
    reasons = OPTIONAL_KEYS.get((slug, path), {})
    doc = _load(slug, "")
    coll = _dig(doc, path)
    entries = {k: v for k, v in coll.items() if isinstance(v, dict)}
    union = set().union(*(set(v) for v in entries.values()))
    for key, reason in reasons.items():
        assert reason and len(reason) > 20, f"{slug}.{path}.{key} exempted without a reason"
        assert key in union, (
            f"{slug}.{path} exempts {key}, which no entry carries any more. "
            "Delete the exemption so the check keeps its teeth.")


@pytest.mark.parametrize("loc", LOCALES)
def test_model_capabilities_universal_keys_are_never_null(loc):
    """A null decodes the same as missing against a non-optional type. The
    cost fields are the known exception: they are null for the free
    on-device model and the file decodes today, so that pair is Double?.
    """
    doc = _load("model-capabilities", loc)
    if doc is None:
        pytest.skip("not shipped")
    models = doc["models"]
    universal = set.intersection(*(set(v) for v in models.values()))
    nullable = {"inputCostPerMillion", "outputCostPerMillion"}
    bad = {
        name: sorted(k for k in universal - nullable if m.get(k) is None)
        for name, m in models.items()
    }
    bad = {n: v for n, v in bad.items() if v}
    assert not bad, (
        f"{bad} in model-capabilities{loc}: null reads as missing to a "
        "non-optional type and discards the whole capabilities file, which "
        "gates the Project Chat gauge on every build including current.")
