"""The served n400_interview_turn prompt config, and what must stay true of it.

Deliberately NOT a set of "the prompt mentions X" assertions. A source-text
assert passes on a prompt that names a rule and contradicts it two
paragraphs later, and this repo has been burned by exactly that shape. What
is pinned here are properties that are checkable and that fail for a real
reason: the model dial resolves through the REAL resolver, the placeholder
vocabulary is complete, the corpus discipline holds, the dossier is
reconciled to the version actually shipped, and no dash punctuation reaches
a served prompt (the model copies the punctuation it sees).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).parent.parent
_CONFIG = _ROOT / "config" / "remote" / "n400" / "interview-turn.json"
_ROUTING = _ROOT / "config" / "remote" / "model-routing.json"
_DOSSIER = _ROOT / "docs" / "prompt-dossiers" / "n400-interview-turn.md"

# Every placeholder the client is expected to fill. A template placeholder
# outside this set is one the client has never been told about, which shows
# up as the literal text "{{whatever}}" reaching a model.
# `user_input` is the request payload (answer.final_text); the rest are read
# by name from request metadata.
_DECLARED = {
    "form_code", "jurisdiction", "locale", "turn_id", "section_label",
    "question_text", "field_ids", "known_facts", "user_input",
    "section_end_instruction",
}


@pytest.fixture(scope="module")
def cfg() -> dict:
    return json.loads(_CONFIG.read_text())


def test_the_config_would_actually_load(cfg):
    """`load_remote_configs` skips any file with no top-level version, and a
    skipped prompt config is an absent one: the call would fall back to
    whatever the client last had rather than failing."""
    assert isinstance(cfg.get("version"), int)
    assert cfg.get("server_only") is True
    assert cfg["systemPrompt"].strip()
    assert cfg["userPromptTemplate"].strip()


def test_every_placeholder_is_one_the_client_was_told_about(cfg):
    used = set(re.findall(r"\{\{(\w+)\}\}", cfg["userPromptTemplate"]))
    assert used == _DECLARED, (
        f"undeclared: {used - _DECLARED}; declared but unused: {_DECLARED - used}")


def test_the_section_end_instruction_covers_every_wire_locale(cfg):
    """en/es/pt are the whole wire vocabulary (their 'pt' ruling). A missing
    one means the summarize-and-confirm ruling silently stops applying for
    that language."""
    for locale in ("en", "es", "pt"):
        assert cfg["sectionEndInstruction"][locale].strip()


def test_the_few_shot_corpus_stays_empty_until_it_is_real(cfg):
    """Two real utterances exist. That is not a corpus.

    Invented speech-shaped examples would teach one narrow idea of speech,
    which is the same failure as the hand-written date pattern that made the
    interview re-ask forever. This does not forbid few-shots; it forbids
    UNSOURCED ones. Once a real export lands, each example carries the
    verbatim utterance it came from and this test passes.
    """
    for shot in cfg.get("fewShots") or []:
        assert (shot.get("utterance") or "").strip(), (
            "a few-shot example with no source utterance is invented speech")


@pytest.mark.parametrize("field", ["systemPrompt", "userPromptTemplate"])
def test_no_dash_punctuation_reaches_the_model(cfg, field):
    """Standing rule for every served prompt AND its output: the model
    copies the punctuation it sees, so an em dash in the instructions comes
    back in the reply the applicant reads."""
    text = cfg[field]
    assert "—" not in text, "em dash in served prompt"
    assert "–" not in text, "en dash in served prompt"


def test_no_dash_punctuation_anywhere_in_the_document(cfg):
    blob = json.dumps(cfg, ensure_ascii=False)
    assert "—" not in blob and "–" not in blob


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
    """N-400 declares `default` only, because no per-call entitlement of
    theirs reaches GP and there is no tier name a per-tier dial could key
    on. Run it through the ACTUAL resolver rather than reading the JSON: a
    dial that parses and does not resolve is the failure worth catching.
    """
    from app.routers.chat import _resolve_model_routing

    configs = {"model-routing": routing}
    fallback = SimpleNamespace(default_model="anthropic/should-not-be-used")
    for tier_name in ("free", "plus", "pro", "automation", "anything"):
        model = _resolve_model_routing(
            _fake_request(configs, "n400"),
            _Body(call_type="n400_interview_turn"),
            fallback, tier_name,
        )
        assert model == "anthropic/claude-sonnet-5", tier_name


def test_an_unregistered_n400_call_type_falls_back_rather_than_dying(routing):
    from app.routers.chat import _resolve_model_routing

    configs = {"model-routing": routing}
    fallback = SimpleNamespace(default_model="anthropic/fallback-model")
    model = _resolve_model_routing(
        _fake_request(configs, "n400"),
        _Body(call_type="n400_not_built_yet"),
        fallback, "free",
    )
    assert model == "anthropic/fallback-model"


def test_registering_n400_did_not_move_another_app_off_its_dial(routing):
    """Adding a tenant to a SHARED config file. The failure worth catching
    is not a bad n400 row, it is a good one that displaced somebody."""
    from app.routers.chat import _resolve_model_routing

    configs = {"model-routing": routing}
    fallback = SimpleNamespace(default_model="anthropic/fallback-model")
    tr = _resolve_model_routing(
        _fake_request(configs, "techrehearsal"),
        _Body(call_type="tr_analysis"), fallback, "free",
    )
    assert tr and tr != "anthropic/fallback-model"


# --- dossier reconciliation -------------------------------------------------

def test_the_dossier_is_reconciled_to_the_version_that_ships(cfg):
    """Same property ops/prompt_watchdog.py enforces against the live
    overlay, checked here against the bundle so a version bump that forgets
    its dossier fails in CI rather than in a watchdog nobody is reading."""
    front = _DOSSIER.read_text().split("---")[1]
    served = int(re.search(r"served_version:\s*(\d+)", front).group(1))
    slug = re.search(r"config_slug:\s*(\S+)", front).group(1)
    assert slug == "n400/interview-turn"
    assert served == cfg["version"], (
        f"dossier says v{served}, config ships v{cfg['version']}")


# --- the config is reachable at all -----------------------------------------

def test_the_call_type_is_registered_or_nothing_reads_this_file():
    """The defect this test exists for.

    interview-turn.json is `server_only`, so /v1/config 404s it on purpose.
    If the call_type is also absent from _CALL_TYPE_TO_CONFIG then
    assemble_prompt returns None and the request proceeds with NO system
    prompt: an unguarded model answering immigration questions. The config
    shipped in that state for the length of one PR review.
    """
    from app.services.prompt_assembly import _CALL_TYPE_TO_CONFIG

    assert _CALL_TYPE_TO_CONFIG.get("n400_interview_turn") == "n400/interview-turn"


def test_every_required_variable_is_actually_used_by_the_template(cfg):
    """A required variable the template never substitutes is a refusal with
    no purpose: turns get 422'd for omitting a value that would have changed
    nothing."""
    used = set(re.findall(r"\{\{(\w+)\}\}", cfg["userPromptTemplate"]))
    assert set(cfg["requiredVariables"]) <= used


def test_the_payload_is_not_in_the_required_list(cfg):
    """{{user_input}} is the request body, not metadata. Requiring it would
    reject every turn, because the check reads the metadata bag."""
    assert "user_input" not in cfg["requiredVariables"]


def test_the_optional_section_instruction_is_not_required(cfg):
    """It is empty except at a section boundary. Requiring it would 422 every
    mid-section turn, which is most of them."""
    assert "section_end_instruction" not in cfg["requiredVariables"]


def test_the_jurisdiction_axis_exists_and_is_empty(cfg):
    """Present so the variant point is discoverable in the dashboard editor,
    empty because the base prompt is correct everywhere until a location
    needs different words. An entry here must be a partial config, never a
    bare string."""
    juris = cfg["jurisdictions"]
    assert isinstance(juris, dict)
    for key, override in juris.items():
        assert isinstance(override, dict), f"{key} must be a partial config"
        assert override, f"{key} overrides nothing"
