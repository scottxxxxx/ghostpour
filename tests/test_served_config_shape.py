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


# --- locale coverage: SS's rule 2, one level up ----------------------
#
# The missing Japanese instruction file was one entry lacking what its
# siblings have, at FILE granularity instead of key granularity, and it
# hid for exactly the same reason: nothing compared a config against its
# peers. Tier copy ships four languages, instruction text ships three, and
# no check noticed.
#
# So the rule generalizes: a served config covers the same locale set its
# siblings cover, or it declares which locales it does not and why. A gap
# is allowed. A silent gap is not.

SHIPPED_LOCALES = {"en", "es", "ja", "fr"}

# Slugs that are structural rather than prose, so they carry no locale
# variants at all. Each needs a reason, same discipline as OPTIONAL_KEYS.
NO_LOCALE_NEEDED = {
    "entitlements": "A tier/state matrix. Carries no user-facing prose.",
    "model-routing": "Server-side routing table. Never reaches a user.",
    "prompt-envelope": "Composition recipe. Section ids are machine tokens, not prose.",
}

# Known, accepted gaps. Each is a translation owed, tracked here rather
# than in somebody's head.
DECLARED_LOCALE_GAPS: dict[str, dict[str, str]] = {
    # Empty on purpose, and it took until 2026-08-23 to get here.
    #
    # protected-prompts.ja: declared owed 2026-08-03 ("Japanese users
    # receive the entire English instruction block"). Paid 2026-08-23, the
    # day Scott found it from the other end, after the Help Me Respond fix
    # shipped to three locales and he asked why not four.
    #
    # canned-report.fr: declared owed 2026-07-27. Paid the same day.
    #
    # report-strings.fr: declared 2026-07-27, paid 2026-08-21.
    #
    # The lesson those three dates carry: a declared gap is a debt with a
    # tracking number and no due date, and "translation owed" stayed true
    # for twenty days with nobody owing it. tests/test_locale_parity.py
    # now holds every localised slug to every locale AND to the English
    # key set, so the next gap is a red test on the PR that opens it, not a
    # declaration that waits for someone to notice.
}


def _locale_coverage():
    import re
    import collections
    cov = collections.defaultdict(set)
    for f in _REMOTE.glob("*.json"):
        m = re.match(r"^(.+?)(?:\.(es|ja|fr))?\.json$", f.name)
        cov[m.group(1)].add(m.group(2) or "en")
    return cov


def test_every_locale_gap_is_declared():
    """A config missing a language its siblings have must say so here."""
    undeclared = {}
    for slug, have in sorted(_locale_coverage().items()):
        if slug in NO_LOCALE_NEEDED:
            continue
        missing = SHIPPED_LOCALES - have
        declared = set(DECLARED_LOCALE_GAPS.get(slug, {}))
        if missing - declared:
            undeclared[slug] = sorted(missing - declared)
    assert not undeclared, (
        f"{undeclared} ship fewer languages than their siblings without "
        "saying so. Add the translation, or declare the gap with a reason "
        "so it is a decision rather than an oversight.")


def test_declared_gaps_are_real_and_reasoned():
    """A declaration that no longer matches reality is worse than none: it
    says somebody looked, when the check has actually stopped applying."""
    cov = _locale_coverage()
    for slug, gaps in DECLARED_LOCALE_GAPS.items():
        assert slug in cov, f"{slug} declares gaps but is not a served config"
        for loc, reason in gaps.items():
            assert len(reason) > 30, f"{slug}.{loc} declared without a reason"
            assert loc not in cov[slug], (
                f"{slug}.{loc} is declared missing but now exists. Delete the "
                "declaration so the check keeps its teeth.")


def test_structural_configs_justify_having_no_locales():
    for slug, reason in NO_LOCALE_NEEDED.items():
        assert len(reason) > 20, f"{slug} exempted from locales without a reason"
        assert slug in _locale_coverage(), f"{slug} is not a served config"


# --- knobs adopted from the client binary (2026-08-03) ---------------
#
# SS audited what is still frozen in their app that GP could own. These
# are the plain-integer ones: no decode risk, and they reach the model on
# every chat send. Values reproduce today's hardcoded ones exactly,
# because adoption has to be byte-identical before anything is tuned.


def test_chat_sizing_knobs_are_served_at_todays_values():
    cc = _load("client-config", "")
    chat = cc["chat"]
    assert chat["max_history_pairs"] == 6
    assert chat["response_reserve_tokens"] == 1024
    assert chat["user_content_reserve_tokens"] == 4000
    assert chat["context_window_fallback_tokens"] == 32000


def test_image_sizing_has_exactly_one_source():
    """Already served per tier. A second copy in client-config would be two
    paths writing the same field, which is how the free-tier copy drifted
    for weeks without anyone noticing."""
    cc = _load("client-config", "")
    assert "image" not in cc.get("chat", {}), (
        "image sizing belongs to tiers.feature_definitions.images only")
    tiers = _load("tiers", "")
    for name, tier in tiers["tiers"].items():
        img = (tier.get("feature_definitions") or {}).get("images")
        assert img and img.get("max_long_edge") and img.get("jpeg_quality"), name


def test_the_prompt_reserve_floor_is_distinct_from_the_default():
    """A floor and a default are different numbers doing different jobs:
    the default is what we reserve absent better information, the floor is
    what the gauge math may never go below."""
    mc = _load("model-capabilities", "")
    assert mc["minPromptReserveTokens"] == 4096
    assert mc["defaultPromptReserveTokens"] == 8000
    assert mc["minPromptReserveTokens"] < mc["defaultPromptReserveTokens"]


def test_the_transcript_word_floor_was_already_served():
    """SS listed this as baked in and unreachable. It has been served all
    along at the same value, which makes it a wiring gap rather than a
    missing knob."""
    cc = _load("client-config", "")
    assert cc["post_session"]["analysis_min_words"] == 300


# --- category is load bearing (2026-08-04) ---------------------------
#
# We told SS to key "this feature is internal, never render a CTA" off
# `category` rather than off an empty or absent upgrade_cta, because a
# positive signal survives copy drift and an absence does not.
#
# SS then told us what that costs. `category` is the ONE field they still
# treat as required, so a definition arriving without it drops itself from
# the catalog. And their gate fails OPEN on a missing definition, on
# purpose: a paid feature with no upgrade path is worse than a stray
# prompt. Compose those and an internal feature that lost its category
# would start rendering exactly the CTA the signal exists to suppress.
#
# The sibling-key test above does not cover this. It only fires when SOME
# sibling carries the key, so it would miss the case where a bad edit
# stripped category from every entry at once.


@pytest.mark.parametrize("loc", LOCALES)
def test_every_feature_definition_carries_a_category(loc):
    doc = _load("tiers", loc)
    if doc is None:
        pytest.skip("not shipped")
    missing = [name for name, entry in doc["feature_definitions"].items()
               if not entry.get("category")]
    assert not missing, (
        f"{missing} in tiers{loc} have no category. The client drops a "
        "definition without one, and its gate fails open on a missing "
        "definition, so an internal feature would start showing the upgrade "
        "CTA that category exists to suppress.")


def test_the_features_registry_agrees():
    """/v1/tiers builds a definition from features.yml when the tiers config
    has no entry, so the fallback path needs a category too."""
    import yaml
    features = yaml.safe_load(
        (Path(__file__).parent.parent / "config" / "features.yml").read_text())["features"]
    missing = [n for n, f in features.items() if not f.get("category")]
    assert not missing, f"{missing} in features.yml have no category"


def test_internal_features_are_marked_as_such():
    """The suppression signal only works if the features it protects
    actually carry it."""
    import yaml
    features = yaml.safe_load(
        (Path(__file__).parent.parent / "config" / "features.yml").read_text())["features"]
    for name in ("tag_centroids", "speaker_consolidation"):
        assert features[name]["category"] == "internal", (
            f"{name} is not user-facing and must stay category=internal, or "
            "the client will render an upgrade CTA for it")
