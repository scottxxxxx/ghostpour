"""The served n400_interviewer_turn prompt config, and what must stay true of it.

The conversation-owning lane (Scott, 2026-09-04: all conversational
intelligence lives in GP). Same discipline as the extractor lane's file:
properties that fail for a real reason, not "the prompt mentions X". The
one source-text family kept on purpose is the DECODER CONTRACT, the JSON
keys and the intent vocabulary the client parses, because those are wire
tokens rather than prose.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).parent.parent
_CONFIG = _ROOT / "config" / "remote" / "n400" / "interviewer-turn.json"
_ROUTING = _ROOT / "config" / "remote" / "model-routing.json"
_DOSSIER = _ROOT / "docs" / "prompt-dossiers" / "n400-interviewer-turn.md"

# Every placeholder the client is expected to fill, from the contract's
# request table. `user_input` is the request payload; the rest are read by
# name from request metadata.
_DECLARED = {
    "form_code", "jurisdiction", "locale", "turn_id", "case_id",
    "conversation", "known_facts", "agenda", "section_boundary",
    "applicant_context", "volunteer_fields", "spoken_numerals", "user_input",
}
_REQUIRED = {"form_code", "jurisdiction", "locale", "turn_id",
             "conversation", "known_facts", "agenda"}
_OPTIONAL = {"case_id", "section_boundary", "applicant_context", "volunteer_fields", "spoken_numerals"}

# The contract's intent vocabulary, verbatim. The client journals these and
# the reviewer grades against them.
_INTENTS = (
    "answer", "partial_answer", "volunteered_extra", "dont_know",
    "help_explain", "repeat", "question_back", "legal_question", "control",
    "correction", "off_topic", "small_talk", "language_switch", "noise",
)
_RESPONSE_KEYS = (
    "schema_version", "turn_id", "intent", "reply", "facts", "asking",
    "clarification", "conflict", "section_checkpoint", "escalation",
    "deferred", "complete", "interview_over",
)


@pytest.fixture(scope="module")
def cfg() -> dict:
    return json.loads(_CONFIG.read_text())


def test_the_config_would_actually_load(cfg):
    assert isinstance(cfg.get("version"), int)
    assert cfg.get("server_only") is True
    assert cfg["systemPrompt"].strip()
    assert cfg["userPromptTemplate"].strip()


def test_every_placeholder_is_one_the_client_was_told_about(cfg):
    used = set(re.findall(r"\{\{(\w+)\}\}", cfg["userPromptTemplate"]))
    assert used == _DECLARED, (
        f"undeclared: {used - _DECLARED}; declared but unused: {_DECLARED - used}")


def test_required_and_optional_partition_the_metadata_variables(cfg):
    """Every metadata placeholder is either required (422 without it) or
    declared optional (blanks cleanly). One in neither set survives into the
    prompt as literal braces when the client omits it."""
    assert set(cfg["requiredVariables"]) == _REQUIRED
    assert set(cfg["optionalVariables"]) == _OPTIONAL
    assert _REQUIRED | _OPTIONAL | {"user_input"} == _DECLARED
    assert not (_REQUIRED & _OPTIONAL)


def test_the_payload_is_not_in_the_required_list(cfg):
    assert "user_input" not in cfg["requiredVariables"]


def test_the_conversation_and_agenda_cannot_be_omitted(cfg):
    """The two inputs that make this lane different from the extractor. A
    turn assembled without them is the old lane with a longer prompt."""
    assert {"conversation", "agenda"} <= set(cfg["requiredVariables"])


def test_the_decoder_contract_is_spelled_out(cfg):
    """The client decodes these tokens. A missing one is a response the
    client cannot parse, and the model would look correct."""
    sp = cfg["systemPrompt"]
    for key in _RESPONSE_KEYS:
        assert f'"{key}"' in sp, key
    for intent in _INTENTS:
        assert f'"{intent}"' in sp, intent


def test_the_never_complete_a_missing_piece_rule_carries_over(cfg):
    """The load-bearing rule from the extractor lane (v3), kept verbatim: the
    worked example is the sentence the rule is remembered by."""
    sp = cfg["systemPrompt"]
    assert '"12, 1987." is a day and a year with no month. It is NOT April' in sp
    assert "verbatim span" in sp


def test_thinking_is_disabled_so_the_reply_cannot_be_starved(cfg):
    """Live rows 2026-09-05 03:32Z: two of the first seven turns hit the
    2048 cap inside Sonnet 5's default thinking block, one with no text at
    all, logged as success. `thinking: disabled` keeps the whole budget for
    the JSON reply, the same guard tr_counterpart_turn carries. Checked
    through the real assembler so the key has to reach the request, not
    merely sit in the file."""
    from app.services.prompt_assembly import assemble_prompt

    assert cfg["thinking"] == "disabled"
    out = assemble_prompt(
        "n400_interviewer_turn", "[start of interview]",
        {"n400/interviewer-turn": cfg},
        variables={
            "form_code": "N-400", "jurisdiction": "US-TX", "locale": "en",
            "turn_id": "t_001", "conversation": "[start of interview]",
            "known_facts": "nothing yet", "agenda": "q | Part 1 | f | Q?",
        },
    )
    assert out["thinking"] == "disabled"


def test_the_few_shot_corpus_stays_empty_until_it_is_real(cfg):
    for shot in cfg.get("fewShots") or []:
        assert (shot.get("utterance") or "").strip(), (
            "a few-shot example with no source utterance is invented speech")


@pytest.mark.parametrize("field", ["systemPrompt", "userPromptTemplate"])
def test_no_dash_punctuation_reaches_the_model(cfg, field):
    text = cfg[field]
    assert "—" not in text, "em dash in served prompt"
    assert "–" not in text, "en dash in served prompt"


def test_no_dash_punctuation_anywhere_in_the_document(cfg):
    blob = json.dumps(cfg, ensure_ascii=False)
    assert "—" not in blob and "–" not in blob


def test_every_required_variable_is_actually_used_by_the_template(cfg):
    used = set(re.findall(r"\{\{(\w+)\}\}", cfg["userPromptTemplate"]))
    assert set(cfg["requiredVariables"]) <= used


def test_the_jurisdiction_axis_exists_and_is_empty(cfg):
    juris = cfg["jurisdictions"]
    assert isinstance(juris, dict)
    for key, override in juris.items():
        assert isinstance(override, dict), f"{key} must be a partial config"
        assert override, f"{key} overrides nothing"


# --- assembly through the real function -------------------------------------

def test_an_optional_variable_the_client_omits_blanks_rather_than_leaking(cfg):
    """Run the ACTUAL assembler. section_boundary is absent on most turns;
    the prompt must not reach the model with '{{section_boundary}}' in it."""
    from app.services.prompt_assembly import assemble_prompt

    configs = {"n400/interviewer-turn": cfg}
    out = assemble_prompt(
        "n400_interviewer_turn", "[start of interview]", configs,
        variables={
            "form_code": "N-400", "jurisdiction": "US-TX", "locale": "en",
            "turn_id": "t_001", "conversation": "[start of interview]",
            "known_facts": "nothing yet",
            "agenda": "q_p1_basis | Part 1: Eligibility | p1.eligibility_basis | Why are you eligible?",
        },
    )
    assert out is not None
    assert "{{" not in out["user_content"], out["user_content"]
    assert "[start of interview]" in out["user_content"]
    assert out["max_tokens"] == cfg["maxTokens"]


def test_a_turn_without_the_conversation_is_refused(cfg):
    from app.services.prompt_assembly import MissingPromptVariables, assemble_prompt

    configs = {"n400/interviewer-turn": cfg}
    with pytest.raises(MissingPromptVariables) as exc:
        assemble_prompt(
            "n400_interviewer_turn", "my name is Lucia", configs,
            variables={
                "form_code": "N-400", "jurisdiction": "US-TX", "locale": "en",
                "turn_id": "t_002", "known_facts": "nothing yet",
                "agenda": "q_p2_name | Part 2: Your name | p2.name | What is your full legal name?",
            },
        )
    assert "conversation" in exc.value.missing


# --- the model dial, resolved through the real resolver ---------------------

def _fake_request(configs: dict, app_id: str):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(remote_configs=configs)),
        state=SimpleNamespace(app_id=app_id),
    )


class _Body:
    def __init__(self, **meta):
        self._meta = meta

    def get_meta(self, key):
        return self._meta.get(key)


@pytest.fixture(scope="module")
def routing() -> dict:
    return json.loads(_ROUTING.read_text())


def test_the_dial_resolves_for_every_tier_name(routing):
    from app.routers.chat import _resolve_model_routing

    configs = {"model-routing": routing}
    fallback = SimpleNamespace(default_model="anthropic/should-not-be-used")
    for tier_name in ("free", "plus", "pro", "automation", "anything"):
        model = _resolve_model_routing(
            _fake_request(configs, "n400"),
            _Body(call_type="n400_interviewer_turn"),
            fallback, tier_name,
        )
        assert model == "anthropic/claude-sonnet-5", tier_name


def test_the_extractor_lane_still_dials_where_it_did(routing):
    """Adding a sibling row must not move the lane that is live on Scott's
    phone tonight."""
    from app.routers.chat import _resolve_model_routing

    configs = {"model-routing": routing}
    fallback = SimpleNamespace(default_model="anthropic/fallback-model")
    model = _resolve_model_routing(
        _fake_request(configs, "n400"),
        _Body(call_type="n400_interview_turn"), fallback, "free",
    )
    assert model == "anthropic/claude-sonnet-5"


# --- dossier reconciliation and reachability ---------------------------------

def test_the_dossier_is_reconciled_to_the_version_that_ships(cfg):
    front = _DOSSIER.read_text().split("---")[1]
    served = int(re.search(r"served_version:\s*(\d+)", front).group(1))
    slug = re.search(r"config_slug:\s*(\S+)", front).group(1)
    assert slug == "n400/interviewer-turn"
    assert served == cfg["version"], (
        f"dossier says v{served}, config ships v{cfg['version']}")


def test_the_call_type_is_registered_or_nothing_reads_this_file():
    from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG

    assert _CALL_TYPE_TO_CONFIG.get("n400_interviewer_turn") == "n400/interviewer-turn"
    # And the extractor lane is still mapped: cutover is the client's, later.
    assert _CALL_TYPE_TO_CONFIG.get("n400_interview_turn") == "n400/interview-turn"
