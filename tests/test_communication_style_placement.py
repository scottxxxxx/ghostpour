"""Where the communication-style line sits, and why the order matters.

CQ returns a one-line style profile and we inject it for the two chat
surfaces. It used to be appended AFTER the recall block, which looks
harmless and was not.

Recall is per-turn volatile, so the Anthropic adapter deliberately
leaves it in the UNCACHED tail: caching it writes an entry covering
prefix+recall that changes every turn and can never be read back. That
optimisation only holds while recall is the last thing in the prompt.
Appending style after it made the tail non-empty, which flipped recall
to cached and bought exactly the wasted per-turn write the adapter
exists to avoid. 143 such calls in 30 days.

Injected BEFORE recall, the style rides inside the cached prefix: full
system-prompt steering weight, cached rather than re-sent, and recall
back where the envelope wants it.

Measured 2026-08-17 before deciding: the mechanism does steer (an
extreme profile cut output 45 to 55%), while this moderate line moves
average sentence length about 8%, which is inside run-to-run noise at
n=9 though consistent in direction across three cases. So it was kept
rather than cut, and made free rather than justified.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import ChatRequest
from app.models.user import UserRecord
from app.services.features.context_quilt_hook import ContextQuiltHook
from app.services.providers.anthropic import _build_system_blocks

RECALL = "User prefers brevity. Met with Bob last Tuesday."
STYLE = ("This user communicates in a moderate-length, direct, "
         "semi-formal, moderately technical, professional style.")


def _user(tier: str = "pro") -> UserRecord:
    return UserRecord(id="u-1", apple_sub="apple-u-1", tier=tier,
                      created_at="2026-01-01T00:00:00Z",
                      updated_at="2026-01-01T00:00:00Z")


async def _run(prompt_mode: str = "PostMeetingChat", style: str = STYLE):
    hook = ContextQuiltHook()
    body = ChatRequest(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="BASE INSTRUCTIONS",
        user_content="hi", context_quilt=True,
        metadata={"prompt_mode": prompt_mode},
    )
    with patch("app.services.features.context_quilt_hook.cq.recall",
               new_callable=AsyncMock,
               return_value={"context": RECALL, "matched_entities": [],
                             "communication_style": style}):
        new_body, _ = await hook.before_llm(
            user=_user(), body=body, tier=None,
            feature_state="enabled", skip_teasers=set())
    return new_body


@pytest.mark.asyncio
async def test_the_style_line_comes_before_the_recall_block():
    body = await _run()
    sp = body.system_prompt
    assert STYLE in sp and RECALL in sp
    assert sp.index(STYLE) < sp.index(RECALL), (
        "style landed after recall, which makes the tail non-empty and "
        "flips recall to cached: a per-turn write that can never be read")


@pytest.mark.asyncio
async def test_recall_is_still_the_last_thing_in_the_prompt():
    body = await _run()
    assert body.system_prompt.rstrip().endswith(RECALL.rstrip())


@pytest.mark.asyncio
async def test_the_cache_layout_has_no_wasted_recall_write():
    """The property that actually costs money. Asserts the BUILT blocks
    rather than the string, because the adapter is what decides."""
    body = await _run()
    blocks = _build_system_blocks(body)
    recall_blocks = [b for b in blocks if RECALL in b["text"]]
    assert recall_blocks, [b["text"][:40] for b in blocks]
    assert all("cache_control" not in b for b in recall_blocks), (
        "recall carries a cache breakpoint; that entry covers "
        "prefix+recall, changes every turn, and is never read back")
    # And the style is inside a block that IS cached, which is the win.
    styled = [b for b in blocks if STYLE in b["text"]]
    assert styled and all("cache_control" in b for b in styled), (
        "the style line is being re-sent uncached every turn")


@pytest.mark.asyncio
async def test_no_style_means_recall_is_untouched():
    """Users with no profile must not regress into a suffix either."""
    body = await _run(style="")
    blocks = _build_system_blocks(body)
    assert all("cache_control" not in b for b in blocks if RECALL in b["text"])


@pytest.mark.asyncio
async def test_other_surfaces_get_no_style_line():
    """Scoped to the two chat modes on purpose; a summary or report has
    no conversational tone to match."""
    body = await _run(prompt_mode="AutoSummary")
    assert STYLE not in body.system_prompt


def test_every_localized_client_config_carries_the_no_file_sentence():
    """The sentence is PERSISTED into the stored answer, so a locale
    that lacks it does not fall back gracefully: that user gets the
    English default written permanently into their transcript, where
    neither our text hygiene nor the client's localization can reach it.

    A missing translation is therefore silent by construction, which is
    why it is asserted rather than left to whoever adds the next locale.
    Pinned against the localized offer copy that already exists, so it
    covers exactly the locales we already claim to serve.
    """
    import json
    import pathlib

    base = pathlib.Path("config/remote")
    localized = sorted(base.glob("client-config.*.json"))
    assert localized, "no localized client configs found"
    missing = []
    for p in localized:
        conf = ((json.loads(p.read_text()).get("documents") or {})
                .get("generation") or {}).get("confirmation") or {}
        # Only locales that already localize the generation envelope.
        if "teaser_text" not in conf:
            continue
        if not str(conf.get("no_file_text") or "").strip():
            missing.append(p.name)
    assert not missing, (
        f"{missing} localize the file offer but not the no-file sentence, "
        "so those users get English persisted into their transcript")
