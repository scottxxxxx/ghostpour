"""TR output ceilings, sized against measured demand (2026-08-19).

Ten truncations across three call types over 90 days and exactly ONE was
ever reported, because a truncated body mostly does not look like an
error. TR's `Wire` types are all-optional, so a cut-off report DECODES
into a report with fewer questions: a wrong score rather than a failure.

The cause is ours. `max_tokens` was pinned at a number chosen before
these models thought by default, and `thinking` is absent from the
request on every one of these paths, so the reasoning and the answer
share one budget and the reasoning goes first. Measured: a
`tr_match_analysis` call spent 3,627 of its 4,096 on thinking and had
~469 left to answer in.

So the ceiling has to clear BOTH halves, not just the answer. Each
figure below is the measured worst case over 90 days of prod traffic,
answer tokens (output minus thinking) and thinking tokens taken
separately, and the ceiling is asserted against their sum. The answer
figures for a truncated mode are CENSORED — a call that hit the ceiling
would have written more — which is why the headroom multiple is not 1.0
and why a ceiling that merely fits today's worst case is not enough.

Not covered here on purpose: `tr_response_analysis` / LiveRoundScore.
Its output scales with the number of questions in a session (n=98,
correlation +0.86, range 32 to 8,846 tokens), so it is a distribution
problem rather than a number problem and the fix is per-question or
chunked. See test_the_chunking_call_is_deliberately_left_alone.
"""

import json
import pathlib

import pytest

CONFIG_DIR = pathlib.Path("config/remote/techrehearsal")

# (call_type, prompt_mode, slug, answer_max, thinking_max, n, truncations)
# measured 2026-08-19 over 90 days of prod usage_log, grouped by the
# max_tokens actually sent on the wire.
MEASURED = [
    ("tr_mock_interview", "InterviewQuestionGen", "mock-interview", 3569, 2852, 40, 5),
    ("tr_match_analysis", None, "match-analysis", 1974, 3627, 29, 1),
    ("tr_parse_jd", None, "jd-analysis", 4591, 1804, 27, 0),
    ("tr_compare_reality", None, "compare-reality", 1827, 2059, 12, 0),
    ("tr_response_analysis", "ConversationPracticeScore", "response-analysis",
     2341, 1464, 14, 0),
]

# A ceiling equal to the observed worst case re-truncates on the next
# call that is slightly bigger, and output is billed on what is produced
# rather than on what was allowed, so headroom is close to free. 1.5x is
# the floor of what counts as headroom here.
MIN_HEADROOM = 1.5


def _config(slug):
    return json.loads((CONFIG_DIR / f"{slug}.json").read_text())


def _effective_max_tokens(cfg, mode):
    """What assembly would actually apply: mode override merged over the
    file-level default, exactly as assemble_prompt merges it."""
    merged = dict(cfg)
    if mode:
        merged.update((cfg.get("modes") or {}).get(mode) or {})
    return merged.get("maxTokens")


@pytest.mark.parametrize(
    "call_type,mode,slug,answer_max,thinking_max,n,truncations",
    MEASURED,
    ids=[m[0] + "/" + str(m[1]) for m in MEASURED],
)
def test_the_ceiling_clears_measured_answer_plus_thinking(
    call_type, mode, slug, answer_max, thinking_max, n, truncations
):
    """The two budgets share one number, so the ceiling is checked against
    their sum. Checking it against the answer alone is what made 4,096
    look sufficient for a call whose answers run to 1,974 tokens."""
    cap = _effective_max_tokens(_config(slug), mode)
    demand = answer_max + thinking_max
    assert cap is not None, f"{call_type} has no ceiling at all"
    assert cap >= demand * MIN_HEADROOM, (
        f"{call_type}/{mode}: ceiling {cap} against measured worst case "
        f"{answer_max} answer + {thinking_max} thinking = {demand} "
        f"({n} calls, {truncations} truncated). Headroom "
        f"{cap / demand:.2f}x is below {MIN_HEADROOM}x, so the next call "
        f"slightly larger than the biggest one we have seen truncates, "
        f"and it truncates SILENTLY into a decodable short report.")


@pytest.mark.parametrize(
    "call_type,mode,slug,answer_max,thinking_max,n,truncations",
    MEASURED,
    ids=[m[0] + "/" + str(m[1]) for m in MEASURED],
)
def test_the_ceiling_reaches_the_wire_and_is_not_decoration(
    call_type, mode, slug, answer_max, thinking_max, n, truncations
):
    """A budget in the file is not a budget on the request.

    Assembly runs only for promptless calls and a mode override applies
    only if assembly runs at all, which is how a LiveRoundScore ceiling
    fix was deployed, verified in prod and still never reached the call
    it was written for -- twice. So this asserts the number comes back
    out of assemble_prompt for the exact (call_type, mode) pair it was
    written for, not that it is present in the JSON.
    """
    from app.services.prompt_assembly import assemble_prompt

    cfg = _config(slug)
    assembled = assemble_prompt(
        call_type,
        "USER CONTENT",
        {f"techrehearsal/{slug}": cfg},
        prompt_mode=mode,
    )
    assert assembled is not None, (
        f"assembly returned nothing for {call_type}, so every value we "
        f"serve for it is decoration")
    assert assembled["max_tokens"] == _effective_max_tokens(cfg, mode)
    # ...and against the measured demand directly, so a mode that stops
    # resolving (renamed, moved, dropped) fails HERE too rather than only
    # in the sizing test. Comparing assembly against a helper that reads
    # the same file agrees with itself when both read 4096.
    assert assembled["max_tokens"] >= (answer_max + thinking_max) * MIN_HEADROOM
    assert assembled["system_prompt"].strip(), (
        "a mode override carrying only a budget must not shadow the "
        "file-level systemPrompt away")


def test_the_short_mock_interview_modes_keep_their_small_ceilings():
    """The raise is scoped to the mode that truncates. InterviewHint and
    InterviewModelAnswer answer in hundreds of tokens with thinking
    explicitly disabled, and on those a low cap is doing real work
    against a runaway response rather than starving a real answer."""
    cfg = _config("mock-interview")
    assert cfg["maxTokens"] == 4096, (
        "the file-level default is the guard for modes that have no "
        "override of their own")
    assert cfg["modes"]["InterviewHint"]["maxTokens"] == 700
    assert cfg["modes"]["InterviewModelAnswer"]["maxTokens"] == 1200
    for mode in ("InterviewHint", "InterviewModelAnswer"):
        assert cfg["modes"][mode].get("thinking") == "disabled", (
            f"{mode} relies on thinking being off to fit its budget")


def test_the_scorecard_ceiling_fits_an_UNCOMPRESSED_full_length_mock():
    """InterviewScorecard is the mock-session scorer, and its measured
    maxima understate demand because the model was already paying for
    the fit out of quality.

    Measured 2026-08-19 over all 25 calls: it has scored 1 to 18
    questions on a 4,096 ceiling and never truncated. That clean record
    is not headroom. Average `per_question` entry size:

        1 to 4 questions      977 chars   (~244 tokens)
        17 questions          512 chars

    Same eight fields present at both ends, none dropped, so no schema
    check on either side can see it. The candidate who completes the
    fullest mock gets the thinnest read on each answer, which is a
    quality loss nothing detects rather than a failure anything reports.

    So the ceiling is sized on what an UNCOMPRESSED report needs, not on
    what the compressed ones happened to fit in:

        18 questions x 244 tokens   = 4,392
        session-level fields        =   150
        measured thinking max       =   750
        ------------------------------------
        demand                      = 5,292

    TR's generator is instructed to produce 12 to 18 questions, so 18 is
    the top of the range they ship rather than a pessimistic invention,
    and two real calls have already scored 17 and 18.

    The margin above 1.5x is deliberate and covers the one thing that
    genuinely could grow: reasoning. This mode has only ever spent 750
    tokens before answering, but LiveRoundScore scores the same rubric on
    a comparable input and has spent 5,218. At that level the demand
    would be 4,392 + 150 + 5,218 = 9,760, which this ceiling still
    clears.
    """
    cfg = _config("response-analysis")
    cap = _effective_max_tokens(cfg, "InterviewScorecard")
    assert cap is not None, (
        "InterviewScorecard has no ceiling of its own, so it inherits the "
        "file default that was already binding on it")
    # Grounded on this mode's own output rather than a 4-chars-per-token
    # rule of thumb: measured over its 14 parseable reports, 3.96 chars per
    # token, an uncompressed per_question entry is 992 chars (251 tokens)
    # and the session-level block is 748 chars (189 tokens).
    UNCOMPRESSED_ENTRY = 251
    SESSION_BLOCK = 189
    MAX_QUESTIONS = 18            # top of TR's documented 12-to-18 range
    answer = MAX_QUESTIONS * UNCOMPRESSED_ENTRY + SESSION_BLOCK   # 4,707

    assert cap >= (answer + 750) * MIN_HEADROOM, (
        f"ceiling {cap} against {answer} answer tokens for an uncompressed "
        f"18-question mock plus this mode's measured worst reasoning; below "
        f"{MIN_HEADROOM}x the model goes on trading depth for fit")

    # The binding case, and the reason this is 16,384 rather than the 12,288
    # that the line above alone would justify. Reasoning is the one input
    # that could still grow: this mode has never spent more than 750 tokens
    # before answering, but LiveRoundScore scores the SAME rubric and has
    # spent 5,218. Nothing about this mode forbids that, the ceiling is an
    # allowance rather than a bill, and the failure it prevents is invisible
    # in production, so the pessimistic case gets the full bar too.
    FAMILY_WORST_REASONING = 5218
    assert cap >= (answer + FAMILY_WORST_REASONING) * MIN_HEADROOM, (
        f"ceiling {cap} does not clear {answer + FAMILY_WORST_REASONING} at "
        f"{MIN_HEADROOM}x, which is this report with reasoning at the level "
        f"the same rubric already reaches on the live path")


def test_the_short_response_analysis_mode_keeps_the_file_default():
    """InterviewFollowUp decides whether to ask one follow-up and answers
    in ~140 tokens. The file-level 4096 is its runaway guard and the
    scorecard raise must not lift it by inheritance, which is why the
    scorecard got a mode of its own instead of the default moving."""
    cfg = _config("response-analysis")
    assert cfg["maxTokens"] == 4096
    assert "maxTokens" not in cfg["modes"]["InterviewFollowUp"]
    from app.services.prompt_assembly import assemble_prompt
    assembled = assemble_prompt("tr_response_analysis", "X",
                                {"techrehearsal/response-analysis": cfg},
                                prompt_mode="InterviewFollowUp")
    assert assembled["max_tokens"] == 4096


def test_the_chunking_call_is_deliberately_left_alone():
    """`tr_response_analysis` / LiveRoundScore stays at 16,384.

    Its four truncations all happened at the OLD 4,096 ceiling on
    2026-08-07/08; every call since the raise has landed inside 16,384
    (n=6, worst 8,846, 54% of cap). So the raise worked and there is
    nothing to fix tonight. What remains is that its output tracks the
    number of questions in a session rather than any one input, so no
    fixed number is safe forever, and the answer is a per-question cap or
    a chunked call agreed with TR rather than a bigger constant here.

    This test exists so a later ceiling sweep does not quietly fold it in
    with the rest and call the distribution problem solved.
    """
    cfg = _config("response-analysis")
    assert cfg["modes"]["LiveRoundScore"]["maxTokens"] == 16384, (
        "changing this number is a two-sided decision with TR, not a "
        "ceiling bump")


def test_every_raised_config_declares_a_new_version():
    """The overlay is served, not the bundle, and a value change does not
    auto-hydrate. A version that did not move is the tell that a sync was
    never done, so the number has to move with the value."""
    expected = {"mock-interview": 17, "match-analysis": 15,
                "jd-analysis": 13, "compare-reality": 10,
                "response-analysis": 21}
    for slug, version in expected.items():
        assert _config(slug)["version"] == version, slug
