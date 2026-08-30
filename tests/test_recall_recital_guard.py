"""A turn that cannot use the recall block must not recite it.

2026-08-30: a 31 second recording tagged to an unrelated project produced a
summary that explained its own emptiness by LISTING the injected context,
naming a real customer engagement, individuals, an overdue commitment and a
promotion decision, none of which appeared in the recording. Recall scoping
is CQ's separate and larger piece of work. This is GP's half, and it holds
regardless of how the scoping lands: shrinking the blast radius does not
make reciting safe.

What is testable here is that the instruction reaches the model, on every
injection path, positioned so it cannot be read as part of the recalled
material, and WITHOUT regressing the Anthropic cache layout. Whether the
model then obeys it is a model-behaviour property that no unit test can
assert; that is verified by replaying the real leaking turn.
"""

from app.models.chat import ChatRequest
from app.services.features.context_quilt_hook import (
    RECALL_USE_GUARD,
    _inject_recall_block,
)
from app.services.providers.anthropic import _build_system_blocks
from tests.conftest import chat_request


def _guard() -> str:
    """The guard text, with a non-vacuity check welded on.

    Every placement assertion in this file uses the guard as a NEEDLE, and
    an empty needle is found everywhere: `"" in s` is True, and `s.index("")`
    is 0, which sorts below any other index. So an emptied or stubbed
    constant would satisfy "guard is present" and "guard comes first"
    simultaneously, and the whole file would go green on a broken fix.

    Not hypothetical. Sabotage 2026-08-30 replaced the constant with `""`
    and six of eight tests stayed green, including EVERY placement test.
    The only one that caught it did so through a source-text substring
    assertion, which is the same shape this codebase has already proven
    blind elsewhere. So no test here takes the needle without first
    proving it is one.
    """
    assert RECALL_USE_GUARD, "guard is empty; every placement test below is vacuous"
    assert len(RECALL_USE_GUARD) > 200, "guard looks stubbed"
    assert "[HOW TO USE THE CONTEXT BELOW]" in RECALL_USE_GUARD
    return RECALL_USE_GUARD


def _req(system_prompt: str) -> ChatRequest:
    return ChatRequest(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        system_prompt=system_prompt,
        user_content="Summarize this recording.",
    )


# --- the guard reaches the model on both placement paths ---

def test_guard_precedes_block_on_append_path():
    """No {{context_quilt}} in the template, so the block is appended.
    The guard must land BEFORE it: after the block it would read as part
    of the recalled material rather than as instruction about it."""
    guard = _guard()
    block = "Don owes a status update on the CTS promotion."
    out = _inject_recall_block(_req("BASE RULES."), block)

    assert guard in out.system_prompt
    assert out.system_prompt.index(guard) < out.system_prompt.index(block)
    # The base prompt is untouched and the block still arrives intact.
    assert "BASE RULES." in out.system_prompt
    assert block in out.system_prompt


def test_guard_precedes_block_on_placeholder_path():
    """When the client template carries the placeholder, the guard fills
    in alongside the block, still ahead of it."""
    guard = _guard()
    block = "Don owes a status update on the CTS promotion."
    out = _inject_recall_block(
        _req("BASE RULES.\n\n{{context_quilt}}\n\nAnswer the user."), block)

    assert "{{context_quilt}}" not in out.system_prompt
    assert guard in out.system_prompt
    assert out.system_prompt.index(guard) < out.system_prompt.index(block)
    assert "Answer the user." in out.system_prompt


def test_guard_forbids_naming_context_only_entities():
    """The load-bearing clause. A blanket ban on the names would be wrong:
    in a meeting that genuinely IS about that engagement the same names are
    grounded in the transcript and belong in the summary. The rule has to
    turn on where the name came from, so pin that it does."""
    assert "but not in the material itself" in RECALL_USE_GUARD
    assert "Listing what the context contained" in RECALL_USE_GUARD


def test_guard_carries_no_dash_punctuation():
    """Served prompt text: the model copies the punctuation it sees."""
    assert "—" not in RECALL_USE_GUARD
    assert "–" not in RECALL_USE_GUARD


# --- the guard must not cost a cache write ---

def test_append_path_keeps_recall_in_the_uncached_tail():
    """The regression this fix could have caused, and the reason the guard
    goes before rather than after.

    The adapter slices system_prompt at the stashed recall text. Recall is
    per-turn volatile, so a breakpoint AFTER it buys a cache entry covering
    prefix+recall that changes every turn and can never be reused. Guard
    text placed after the block would turn the empty suffix non-empty and
    do exactly that. Assert the layout the adapter documents: two blocks,
    recall last, no cache_control on it."""
    guard = _guard()
    block = "Don owes a status update on the CTS promotion."
    out = _inject_recall_block(_req("BASE RULES."), block)

    blocks = _build_system_blocks(out)

    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert guard in blocks[0]["text"], \
        "guard belongs in the cached prefix, not the volatile tail"
    assert blocks[1]["text"] == block
    assert "cache_control" not in blocks[1], \
        "a breakpoint after volatile recall is a cache write bought for nothing"


def test_stashed_block_still_locates_inside_the_prompt():
    """The adapter finds the recall text by substring. If the guard were
    ever folded INTO the stashed value, or the stash drifted from what was
    inserted, the split would silently fall back to one block and the
    prefix would stop caching. Pin the stash to the bare block."""
    block = "Don owes a status update on the CTS promotion."
    out = _inject_recall_block(_req("BASE RULES."), block)

    assert out.get_meta("cq_recall_block") == block
    assert out.system_prompt.find(block) >= 0


# --- end to end, through the real request path ---

def test_guard_present_on_a_live_cq_chat_turn(
    client_with_cq, pro_user, mock_provider, mock_cq
):
    """Through /v1/chat rather than the helper, so a refactor that stops
    routing recall through _inject_recall_block still gets caught."""
    resp = client_with_cq.post(
        "/v1/chat",
        json=chat_request(user_content="Summarize this recording.",
                          context_quilt=True),
        headers=pro_user["headers"],
    )
    assert resp.status_code == 200

    guard = _guard()
    sent = mock_provider.await_args_list[-1].args[0]
    assert guard in sent.system_prompt
    # And it is still ahead of the recalled material the fixture returns.
    assert sent.system_prompt.index(guard) < \
        sent.system_prompt.index("User prefers concise answers")


def test_no_guard_when_no_context_was_injected(
    client, pro_user, mock_provider
):
    """No block, no instruction about a block. A standing line telling the
    model not to recite context it was never given is noise in every
    non-CQ prompt, and pays cache cost on every surface."""
    resp = client.post(
        "/v1/chat",
        json=chat_request(user_content="Summarize this recording."),
        headers=pro_user["headers"],
    )
    assert resp.status_code == 200
    assert _guard() not in \
        mock_provider.await_args_list[-1].args[0].system_prompt
