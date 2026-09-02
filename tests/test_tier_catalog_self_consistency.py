"""The tier catalog must not contradict itself about who gets a feature.

Found in Shoulder Surf simulator QA, 2026-09-02, on the LIVE catalog
(tiers v57): Free's bullet said "Web search: 5 a month" and Free's dial said
`searches_per_month: 5`, while three other strings in the same document said
the feature was paid. All four locales carried the same contradiction:

    free.feature_bullets            "Web search: 5 a month"          (grants)
    free.feature_definitions.search  searches_per_month: 5           (grants)
    feature_definitions.search.teaser_description
                                     "Web search is a paid feature."  (denies)
    feature_definitions.search.upgrade_cta
                                     "Upgrade to Plus to enable ..."  (denies)
    free...search.cta_hard_cap.footer
                                     "available with a Plus or Pro
                                      subscription"                   (denies)

The ruling is Scott's tier matrix (`Subscription Info/`, 2026-08-20): Free
gets 5 per month, Plus 75, Pro 120. So the bullets were right and the copy
was stale, left over from the pre-08-20 state where Free had none. The app
worked around it by refusing to state either reading, which is a client
covering for a server that cannot make up its mind.

Nothing caught it because nothing compared the two halves. `test_locale_parity`
checks that every locale has the same KEYS, which all four did, identically
wrong.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

_REMOTE = pathlib.Path(__file__).parent.parent / "config" / "remote"
_TIERS = ["tiers.json", "tiers.es.json", "tiers.fr.json", "tiers.ja.json"]
_HIGHLIGHTS = ["feature-highlights.json", "feature-highlights.es.json",
               "feature-highlights.fr.json", "feature-highlights.ja.json"]

# The exact strings that were live and wrong. Pinned as forbidden rather than
# described, because "copy that denies the feature" is not something a test
# can recognise in prose, and these are the ones that actually shipped.
_STALE = [
    "Web search is a paid feature",
    "Upgrade to Plus to enable web search",
    "available with a Plus or Pro subscription",
    "La búsqueda web es una función de pago",
    "para activar la búsqueda web",
    "está disponible con una suscripción Plus o Pro",
    "La recherche web est une fonctionnalité payante",
    "pour activer la recherche web",
    "est disponible avec un abonnement Plus ou Pro",
    "Web検索は有料機能です",
    "してWeb検索を有効にする",
]


def _load(name: str) -> dict:
    return json.loads((_REMOTE / name).read_text(encoding="utf-8"))


def _search_bullet(doc: dict) -> str:
    free = doc["tiers"]["free"]
    for b in free["feature_bullets"]:
        low = b.lower()
        if "web search" in low or "búsqueda web" in low or "recherche web" in low \
                or "検索" in b:
            return b
    raise AssertionError("no web-search bullet on the Free tier")


@pytest.mark.parametrize("name", _TIERS)
def test_the_free_bullet_and_the_free_dial_agree_on_the_number(name):
    """The load-bearing check, and a real cross-field one rather than prose
    matching: the number Free is TOLD it gets and the number Free IS given
    come from two different places in the file and nothing compared them."""
    doc = _load(name)
    dial = doc["tiers"]["free"]["feature_definitions"]["search"]["searches_per_month"]
    assert isinstance(dial, int) and dial > 0, (
        f"{name}: Free's search dial is {dial!r}; if Free genuinely has no "
        f"web search then the bullet is the thing to change, not this test")
    claimed = re.search(r"\d+", _search_bullet(doc))
    assert claimed, f"{name}: the Free web-search bullet states no number"
    assert int(claimed.group()) == dial, (
        f"{name}: bullet says {claimed.group()}, dial gives {dial}")


@pytest.mark.parametrize("name", _TIERS + _HIGHLIGHTS)
def test_no_served_copy_says_web_search_is_paid_only(name):
    """Free gets 5 a month. Any string still saying the feature requires an
    upgrade to exist contradicts the dial in the same document."""
    blob = (_REMOTE / name).read_text(encoding="utf-8")
    found = [s for s in _STALE if s in blob]
    assert not found, f"{name} still carries: {found}"


@pytest.mark.parametrize("name", _HIGHLIGHTS)
def test_the_highlight_does_not_tag_web_search_as_plus_and_pro(name):
    """`(Plus and Pro)` on the highlight said the same thing the catalog
    denied. The tag may say Plus and Pro give MORE; it may not say they are
    the only plans that have it."""
    doc = _load(name)
    hit = [h for h in doc["highlights"] if h.get("id") == "search"]
    assert len(hit) == 1, name
    text = hit[0]["text"]
    for exact in ("(Plus and Pro)", "(Plus y Pro)", "(Plus et Pro)", "（PlusとPro）"):
        assert exact not in text, f"{name}: highlight still reads {text!r}"


@pytest.mark.parametrize("name", _TIERS)
def test_every_locale_states_the_same_free_allowance(name):
    """A number that is a dial has to move in all four files together. The
    contradiction shipped in four languages at once because nobody compared
    them, and the number is the half most likely to be edited alone."""
    assert _load(name)["tiers"]["free"]["feature_definitions"]["search"][
        "searches_per_month"] == _load("tiers.json")["tiers"]["free"][
        "feature_definitions"]["search"]["searches_per_month"]
