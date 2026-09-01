"""The live log records the request's query string (2026-09-01).

It did not, for any request, ever. `StreamingBypassMiddleware` is the only
logging middleware main.py adds, and its buffer entry had no `query` key;
the sibling class that sets one, `RequestLoggingMiddleware`, is explicitly
NOT added. So correct-looking code sat next to the live path and never ran,
and the one instrument built to answer "what did the client actually send"
was blind to every row-selecting param: `?since=`, `?delta=true`, `?limit=`,
`?offset=`, `?project_id=`.

That is the failure these tests exist to prevent recurring, and it is
precisely the class of bug the query log is meant to CATCH: a param dropped
on the middle hop, invisible from both endpoints.
"""

from app.middleware import request_logging as rl


def test_the_query_is_recorded_at_all():
    assert rl._format_query(b"project_id=p-1") == "project_id=p-1"
    assert rl._format_query(b"name=Foo&project_id=p-1") == "name=Foo&project_id=p-1"


def test_absent_and_empty_query_read_as_none_not_empty_string():
    """None means 'no query'. An empty string would render in the dashboard
    as a param list that exists and is blank, which is a different claim."""
    assert rl._format_query(None) is None
    assert rl._format_query(b"") is None


def test_the_double_space_survives_url_encoded():
    """The resolve route's whole signal is an exact stored name, and
    `Immigration  Interview App` has two spaces. Percent-encoding must not
    be normalised on its way into the log."""
    q = b"name=Immigration%20%20Interview%20App"
    assert rl._format_query(q) == "name=Immigration%20%20Interview%20App"


def test_sensitive_params_are_redacted_by_the_same_rule_as_bodies():
    """A query string is no less likely to carry a token than a body is, so
    it uses `_is_sensitive_key` rather than a second policy that could drift."""
    out = rl._format_query(b"token=abc123&project_id=p-1")
    assert "abc123" not in out
    assert "token=<redacted>" in out
    assert "project_id=p-1" in out, "redaction ate an innocent param"
    for name in ("api_key", "client_secret", "password", "credential"):
        got = rl._format_query(f"{name}=hunter2&keep=1".encode())
        assert "hunter2" not in got, name
        assert "keep=1" in got, name


def test_a_valueless_flag_is_not_mangled():
    assert rl._format_query(b"delta&since=x") == "delta&since=x"


def test_an_absurd_query_is_capped():
    out = rl._format_query(b"a=" + b"x" * 10_000)
    assert len(out) <= rl._MAX_QUERY_LOG


def test_the_middleware_entry_actually_carries_the_key(client):
    """The unit above proves the formatter. This proves the LIVE PATH calls
    it, which is the half that was broken: the formatter could be perfect
    and the entry still omit the key, which is exactly what happened.
    """
    rl._LOG_BUFFER.clear()
    # A real logged route. /health is in _SKIP_PATHS and never reaches the
    # buffer, so using it would have made this test pass vacuously on an
    # empty list if the assertion had been written any weaker.
    client.get("/v1/projects/u1/resolve?project_id=p-1&name=Foo")
    entries = [e for e in rl.get_recent_logs(50) if "/resolve" in (e.get("path") or "")]
    assert entries, "the request never reached the log buffer"
    assert "query" in entries[0], (
        "the buffer entry has NO `query` key. That is the original bug: the "
        "formatter exists, the live middleware does not call it.")
    assert entries[0]["query"] == "project_id=p-1&name=Foo"
