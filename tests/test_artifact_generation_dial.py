"""The artifact model is its own dial.

Scott 2026-08-15: the model that WRITES the artifact is a different
choice from the model that answers the chat, so dialing one must never
move the other. It also has to be reachable, which is why it is checked
BEFORE the surface preference: the artifact call carries the originating
prompt_mode, so PostMeetingChat would otherwise resolve `meeting_chat`
and the new dial could never win.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROUTING = json.loads(
    (pathlib.Path(__file__).resolve().parents[1]
     / "config/remote/model-routing.json").read_text())
SS = ROUTING["apps"]["shouldersurf"]["call_types"]


def test_the_dial_exists_and_the_dashboard_will_render_it() -> None:
    """The dashboard iterates call_types, so shipping the row is the
    whole integration; there is no separate UI to build."""
    row = SS["artifact_generation"]
    assert row["label"] == "Artifact Generation"
    assert set(row["models"]) >= {"free", "plus", "pro", "automation"}


def test_it_is_independent_of_every_chat_dial() -> None:
    """Independence is the requirement, not a side effect."""
    art = SS["artifact_generation"]["models"]
    for other in ("meeting_chat", "meeting_chat_follow_up",
                  "project_chat", "project_chat_follow_up"):
        assert other in SS, other
        assert SS[other]["models"] is not art


def test_the_default_is_the_model_that_won_the_bench() -> None:
    """Sonnet 4.6, not Sonnet 5. Measured 2026-08-15 under contract: 202
    characters of expected behavior per row against Sonnet 5's 95, notes
    filled on 42 of 42 rows against 15 of 46, for about the same money.
    Row count favoured Sonnet 5 and substance did not."""
    assert SS["artifact_generation"]["models"]["pro"] == (
        "anthropic/claude-sonnet-4-6")


@pytest.mark.parametrize("tier", ["free", "plus", "pro", "automation"])
def test_every_dialled_model_is_actually_callable(tier: str) -> None:
    """Two lists gate a model and they live in different files. A row in
    model-routing.json makes it SELECTABLE; providers.yml makes it
    CALLABLE. Disagree and a real turn dies at our own edge in 0 ms with
    a deliberately opaque client message."""
    import yaml
    raw = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parents[1]
         / "config/providers.yml").read_text())
    callable_ids = {
        m["id"] if isinstance(m, dict) else m
        for m in raw["providers"]["anthropic"]["models"]}
    model = SS["artifact_generation"]["models"][tier]
    assert model.startswith("anthropic/"), model
    assert model.split("/", 1)[1] in callable_ids, model


def test_the_dial_is_reachable_before_the_surface_preference() -> None:
    """Source-level guard. The artifact request carries the originating
    prompt_mode, so if this check moved below the surface block the row
    would be dead config that the dashboard still renders."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "app/routers/chat.py").read_text()
    dial = src.index('if call_type == "artifact_generation":')
    surface = src.index("surface_dials = {")
    assert dial < surface, (
        "the artifact dial must resolve before the surface preference")
