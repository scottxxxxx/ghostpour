"""Verify that the stdout formatter renders fields passed via extra={}."""

import logging

from app.main import _ExtraRenderingFormatter


def _format(logger_name: str, msg: str, extra: dict | None = None) -> str:
    fmt = _ExtraRenderingFormatter("%(name)s: %(message)s")
    record = logging.LogRecord(
        name=logger_name,
        level=logging.INFO,
        pathname="t.py",
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return fmt.format(record)


def test_no_extras_unchanged():
    assert _format("app.foo", "hello") == "app.foo: hello"


def test_renders_extras_as_key_value_pairs():
    out = _format("app.services.context_quilt", "cq_recall_ok", {"matched": 3, "patch_count": 12})
    assert out.startswith("app.services.context_quilt: cq_recall_ok ")
    assert "matched=3" in out
    assert "patch_count=12" in out


def test_string_extras():
    out = _format("svc", "cq_recall_error", {"error": "timeout after 5s"})
    assert "error=timeout after 5s" in out


def test_no_reserved_attrs_leak():
    out = _format("svc", "msg", {"matched": 1})
    # Standard LogRecord attrs must not appear
    for forbidden in ("levelno", "pathname", "filename", "funcName", "lineno"):
        assert f"{forbidden}=" not in out
