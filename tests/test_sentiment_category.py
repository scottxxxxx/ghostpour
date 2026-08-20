"""Sentiment gains a category, and the model chooses it exactly once.

Scott's product ruling 2026-08-19: Tense must mean interpersonal friction
only. Action-heavy meetings with nobody at odds were rendering red, by
two separate paths, and neither required friction to exist.

CQ drafted the eight categories and the contrast rules; GP composes them,
per doc 17. The mechanism that makes it stick is that the model now picks
ONE sentiment field. `emoji_label` and `emoji` are DERIVED from it here,
because a report carrying category `pressured` and emoji_label `tense`
would be plausible in both fields independently, would render red on the
legacy mapping, and would look shipped. Derivation makes that
contradiction impossible rather than forbidden.

The evidence gate is the same shape one level down: `tense` is not
allowed without a quotable moment of friction, so the model cannot reach
for it by reading deadline density as tension.
"""

import json
import pathlib
import re

from app.services.meeting_report import (
    _CATEGORY_TO_EMOJI_LABEL,
    derive_sentiment_fields,
)

from app.services.meeting_report import (
    _LOCALE_DIRECTIVE,
    REPORT_SYSTEM_PROMPT,
    REPORT_USER_TEMPLATE,
)

SRC = pathlib.Path("app/services/meeting_report.py").read_text()

# What the MODEL is shown. Asserting against the whole module would also
# catch our own derivation code, which legitimately sets emoji_label: the
# question is whether the model is still ASKED for it, not whether the
# string appears anywhere in the file.
PROMPT = REPORT_SYSTEM_PROMPT + REPORT_USER_TEMPLATE + _LOCALE_DIRECTIVE

CATEGORIES = ["positive", "collaborative", "informational", "cautious",
              "pressured", "tense", "disconnected", "decisive"]


# --- the three-site removal -------------------------------------------

def test_the_model_is_never_asked_for_emoji_label_again():
    """Removal is three sites, not one: the rules line, the schema block,
    and the LANGUAGE keep-in-English list. The third reads as boilerplate
    and is the one that survives into being wrong, which is why this
    asserts absence across the whole file rather than per site."""
    assert "emoji_label" not in PROMPT, (
        "the model is still being instructed about a field it no longer "
        "emits, which is a true-when-written line surviving into wrong")
    assert '"emoji"' not in PROMPT, "the model is still asked for a glyph"


def test_the_schema_asks_for_category_and_its_evidence():
    assert '"category": "string: exactly one of: positive, collaborative' in PROMPT
    assert '"category_evidence"' in PROMPT


def test_the_language_line_keeps_the_new_enum_in_english():
    """Every other wire enum is pinned to English there. A category
    translated into French is a wire value nobody can match on."""
    lang = [l for l in PROMPT.splitlines() if l.startswith("LANGUAGE:")]
    assert lang, "the LANGUAGE line moved; this test needs updating"
    assert "sentiment category (positive, collaborative" in lang[0]
    assert "emoji_label" not in lang[0]


# --- the evidence gate ------------------------------------------------

def test_the_gated_categories_cannot_be_claimed_without_a_quote():
    """The gate is the only mechanism in this block that has demonstrably
    worked, so it is asserted as a property rather than as a sentence.

    Revision 2 extended it from tense alone to three categories, after an
    eval showed the model fleeing a gated label straight into an ungated
    one: tense went to zero and pressured went from one meeting to five.
    A gate on one label just moves the over-reach one rung down.
    """
    for gated in ("tense", "pressured", "disconnected"):
        assert re.search(rf"may not answer {gated}", PROMPT), (
            f"{gated} can be claimed without evidence, so it becomes the "
            f"next rung the model reaches for")
    assert "contract violation" in PROMPT


def test_the_gate_names_what_to_answer_instead():
    """A rule that only forbids leaves the model to invent a way to
    comply. Naming the honest fallbacks is what stops it fleeing into
    another gated label."""
    assert re.search(r"the answer is informational", PROMPT), (
        "the gate forbids without naming what to answer instead, which "
        "leaves the model to pick its own escape hatch")
    # rev3: the fallback is a single named default rather than a LIST.
    # A list of acceptable fallbacks is itself an escape hatch, which is
    # how the over-reach relocated from pressured to collaborative.
    assert "START FROM informational" in PROMPT
    assert "ungated categories are not an escape" in PROMPT


def test_the_contrast_rules_cover_every_confusion_an_eval_has_caught():
    """Each of these is a measured failure, not a hypothetical.

    The first two produced red on meetings with no friction. The last two
    came out of the 2026-08-20 eval table: a standup answered pressured
    because standups discuss ETAs, and a long debugging session answered
    pressured because hard work reads as stress.
    """
    assert "never tense" in PROMPT, "deadline talk can still read as friction"
    assert "tense requires friction between participants" in PROMPT
    assert "SCHEDULING IS NOT PRESSURE" in PROMPT, (
        "an ordinary standup assigning dates will read as pressured again")
    assert "DIFFICULTY IS NOT PRESSURE" in PROMPT, (
        "effortful joint work will read as pressured again")


def test_disagreement_voiced_calmly_still_counts_as_tense():
    """The rule cuts both ways or it just moves the bug: a polite
    disagreement IS friction and must not be softened into pressured."""
    assert "even when it is voiced calmly" in PROMPT


# --- the derivation ---------------------------------------------------

def test_every_category_derives_a_legacy_label():
    """Targets are the legacy vocabulary on purpose, so existing consumers
    keep working and the glyphs can come from a table SS already ships."""
    assert set(_CATEGORY_TO_EMOJI_LABEL) == set(CATEGORIES)
    legacy = {"enthusiastic", "collaborative", "positive", "informational",
              "focused", "cautious", "frustrated", "tense", "concerned",
              "disappointed"}
    for cat, label in _CATEGORY_TO_EMOJI_LABEL.items():
        assert label in legacy, f"{cat} derives {label}, which is not a legacy label"


def test_pressured_does_not_derive_tense():
    """The whole point. If pressured derived tense, the fix would ship and
    the strip would stay red on exactly the meetings it was built for."""
    assert _CATEGORY_TO_EMOJI_LABEL["pressured"] != "tense"
    out = derive_sentiment_fields({"sentiment": {"category": "pressured"}})
    assert out["sentiment"]["emoji_label"] != "tense"


def test_tense_still_derives_tense():
    out = derive_sentiment_fields({"sentiment": {"category": "tense"}})
    assert out["sentiment"]["emoji_label"] == "tense"


def test_an_unrecognised_category_leaves_the_field_absent_rather_than_guessing():
    """A model that invents a category has violated the contract. Filling
    a neutral-looking label would hide that behind a plausible report,
    which is the failure this whole change exists to stop."""
    out = derive_sentiment_fields({"sentiment": {"category": "vibes"}})
    assert "emoji_label" not in out["sentiment"]


def test_a_report_with_no_category_is_left_exactly_as_it_was():
    """An older stored report passed back through the re-render path must
    not acquire fields it never had."""
    before = {"sentiment": {"label": "Steady", "score": 60}}
    after = derive_sentiment_fields(json.loads(json.dumps(before)))
    assert after == before


def test_derivation_runs_once_at_generation_not_on_re_render():
    """The re-render route takes client supplied JSON and only produces
    HTML. Deriving there too would mean two places to keep in step."""
    reports = pathlib.Path("app/routers/reports.py").read_text()
    assert reports.count("derive_sentiment_fields(") == 1, (
        "derivation appears more than once; it is a single step at "
        "generation by design")
    # and it sits before the report is stored
    idx_derive = reports.index("derive_sentiment_fields(report_json)")
    idx_store = reports.index("report_json_str = json.dumps(report_json")
    assert idx_derive < idx_store, "derivation must run before the report is stored"


def test_no_dashes_were_introduced_into_the_served_prompt():
    """House rule, and it matters more here than in prose: the model
    copies the punctuation it is shown."""
    prompt_region = PROMPT[PROMPT.index("- START FROM informational"):]
    prompt_region = prompt_region[:prompt_region.index("REPORT_USER_TEMPLATE")] \
        if "REPORT_USER_TEMPLATE" in prompt_region else prompt_region
    assert not re.search(r"[—–]", prompt_region)


# --- end to end through the real route -------------------------------
#
# CQ asked, correctly, which test proves a GENERATED report carries the
# derived fields. The answer was "none": the test above reads reports.py
# as TEXT and asserts the call appears once in the right place. That is
# the source-text assertion this repo has been burned by, and it cannot
# tell a call that runs from a call that is defined and never reached.
# A defined-but-uncalled derivation is the classic shipped-looking hole.
#
# So this one exercises the actual route with the provider stubbed, and
# asserts the fields on what the route RETURNS.

def test_a_generated_report_comes_back_carrying_the_derived_fields(
        client, pro_user, tmp_db_path, monkeypatch):
    import sqlite3
    import uuid as _uuid
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    from app.models.chat import ChatResponse

    meeting_id = "sentiment-e2e-" + _uuid.uuid4().hex[:8]
    conn = sqlite3.connect(tmp_db_path)
    conn.execute(
        """INSERT INTO meeting_transcripts
           (id, user_id, meeting_id, transcript, project, project_id, created_at)
           VALUES (?, ?, ?, ?, NULL, NULL, ?)""",
        (str(_uuid.uuid4()), pro_user["user_id"], meeting_id,
         "[Speaker 1] We shipped it.\n[Speaker 2] Good.",
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    # A report exactly as the model now answers: category chosen, and NO
    # emoji_label or emoji, because it is no longer asked for either.
    model_json = {
        "header": {"category": "Working Session", "title": "t",
                   "summary": "s", "attendees": []},
        "stoplight": {"color": "green", "label": "ok", "detail": "d"},
        "sentiment": {"score": 55, "label": "Deadline pressure, spirits holding",
                      "detail": "d", "category": "pressured",
                      "category_evidence": "we need this before Friday",
                      "arc": [], "arc_narrative": "n"},
        "suggested_tags": [], "actions": [], "decisions": [],
        "technical_issues": [], "open_questions": [],
        "queries_during_meeting": [],
    }

    async def fake_route(_chat_request):
        return ChatResponse(
            text=json.dumps(model_json), input_tokens=10, output_tokens=20,
            model="claude-sonnet-4-6", provider="anthropic",
            usage={"input_tokens": 10, "output_tokens": 20},
        )

    from app.main import app as _app
    monkeypatch.setattr(_app.state.provider_router, "route",
                        AsyncMock(side_effect=fake_route))

    r = client.post(
        f"/v1/meetings/{meeting_id}/report",
        json={"duration_seconds": 600},
        headers={**pro_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert r.status_code == 200, r.text
    sentiment = r.json()["report_json"]["sentiment"]

    # The model never emitted these. If they are here, the derivation ran.
    assert sentiment["emoji_label"] == "concerned", (
        "a generated report came back without a derived emoji_label, so "
        "the derivation is defined and never reached")
    assert sentiment["emoji"] == "\U0001F61F"
    # and the model's own choice survived alongside them
    assert sentiment["category"] == "pressured"
    assert sentiment["category_evidence"] == "we need this before Friday"
