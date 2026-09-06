"""The interviewer lane must never take the streaming transport.

`_handle_stream` returns from `chat()` before `_run_turn_tail`, so a
streamed interviewer turn would bypass the envelope extraction and retry,
the checkpoint refusal, the oath-modification refusal, and all of
`guard_response_text`. Those guards are the mechanism that keeps an answer
she did not give off a federal form. A transport that skips them is not a
fast path, it is an unguarded one.

Found by auditing GP's response paths after the N-400 client found the
same shape in its own write paths: a floor that one path skips is not a
floor. Measured at the time: 0 of the last 500 interviewer turns set
stream, so this is a latent hole rather than a live one, which is the
moment to close it.
"""
import ast


def _should_stream_source():
    src = open("app/routers/chat.py").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "should_stream"
                for t in node.targets):
            return ast.get_source_segment(src, node.value)
    raise AssertionError("should_stream is no longer a single assignment")


def test_the_interviewer_lane_is_excluded_from_streaming():
    assert 'call_type != "n400_interviewer_turn"' in _should_stream_source(), (
        "a streamed interviewer turn bypasses every N-400 guard")


def test_the_guards_run_after_the_streaming_return_so_the_exclusion_is_load_bearing():
    """Pins WHY the exclusion matters: the guard call sits after the point
    where the streaming path has already returned. If someone ever moves
    the guards ahead of that return, this test should be revisited rather
    than the exclusion quietly kept for no reason."""
    src = open("app/routers/chat.py").read()
    tree = ast.parse(src)
    guard_line = next(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "guard_response_text")
    stream_line = next(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_handle_stream")
    assert stream_line < guard_line, (
        "the streaming return no longer precedes the guards; re-derive "
        "whether the exclusion is still the right fix")


def test_the_other_deliberate_exclusions_are_still_there():
    """The exclusion list is load-bearing for several lanes. If someone
    rewrites it, this says what it was protecting."""
    src = _should_stream_source()
    for guard in ('call_type not in ("summary", "analysis")',
                  "not is_project_chat", "not body.generation",
                  "not _template_id", "not _contract_id"):
        assert guard in src, f"lost a stream exclusion: {guard}"
