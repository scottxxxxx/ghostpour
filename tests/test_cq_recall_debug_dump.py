"""Recall debug dump ring (memory contract v1 lane verification).

CQ's lane test needs GP to prove two things byte-exact: the outbound
/v1/recall body (did memory_signals survive the passthrough) and the
returned block (diffs line for line against CQ's reference render,
byte-stable within a UTC day). The dump ring beside the DB is the
capture point; usage_log's copy of the block is already wrapped for
the LLM and useless for that diff.
"""

import json

from app.services.context_quilt import _RECALL_DUMP_KEEP, _debug_dump_recall


def _set_db(monkeypatch, tmp_path):
    from app import database
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "cloudzap.db"),
                        raising=False)


def test_dump_round_trips_byte_exact(monkeypatch, tmp_path):
    _set_db(monkeypatch, tmp_path)
    sent = {"user_id": "u-1", "text": "q",
            "metadata": {"memory_signals": True, "token_budget": 1200,
                         "project": "Köre"}}
    received = {"context": "[SCOPED BLOCK]\n(no stored memory about: X)\nline",
                "matched_entities": ["Kore"], "patch_count": 3}
    _debug_dump_recall(sent, received)

    files = list((tmp_path / "cq_recall_debug").glob("recall-*.json"))
    assert len(files) == 1
    dump = json.loads(files[0].read_text())
    # exact round trip, non-ascii preserved (ensure_ascii=False)
    assert dump == {"sent": sent, "received": received}
    assert "Köre" in files[0].read_text()


def test_ring_keeps_only_last_n(monkeypatch, tmp_path):
    _set_db(monkeypatch, tmp_path)
    for i in range(_RECALL_DUMP_KEEP + 3):
        _debug_dump_recall({"i": i}, {"context": ""})
    files = sorted((tmp_path / "cq_recall_debug").glob("recall-*.json"))
    assert len(files) == _RECALL_DUMP_KEEP
    # newest survived
    assert json.loads(files[-1].read_text())["sent"] == {"i": _RECALL_DUMP_KEEP + 2}


def test_dump_failure_never_raises(monkeypatch, tmp_path):
    """The dump must never break recall itself."""
    _set_db(monkeypatch, tmp_path)
    # unwritable target: the dump dir path exists as a FILE
    (tmp_path / "cq_recall_debug").write_text("in the way")
    _debug_dump_recall({"a": 1}, {"context": ""})  # must not raise


def test_recall_degrade_is_loud(monkeypatch, caplog):
    """A recall timeout returns empty AND logs at ERROR with context —
    the 2026-07-18 contract-test turn lost its memory block silently and
    was only caught forensically. Degrades must be visible as they
    happen."""
    import asyncio
    import logging
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from app.services import context_quilt as cq

    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.TimeoutException("too slow"))
    monkeypatch.setattr(cq, "_get_client", lambda: client)
    monkeypatch.setattr(cq, "_get_auth_headers", AsyncMock(return_value={}))

    with caplog.at_level(logging.ERROR, logger="app.services.context_quilt"):
        result = asyncio.run(cq.recall(
            user_id="u-1", text="q",
            metadata={"project": "Kore", "memory_signals": True}))

    assert result == {"context": "", "matched_entities": [], "patch_count": 0}
    degraded = [r for r in caplog.records if "cq_recall_degraded" in r.getMessage()]
    assert len(degraded) == 1
    assert degraded[0].levelno == logging.ERROR
    assert degraded[0].project == "Kore"
    assert degraded[0].memory_signals is True
