"""MetricKit crash and hang intake: POST /v1/diagnostics/crash and the admin
read side under /webhooks/admin/crash-reports.

The payload tests send RAW bytes, not `json=`, because the claim under test
is about the text on the wire: what the device sent is what the row holds,
byte for byte. A test that let httpx serialize a dict would only prove that
two serializers agree with each other.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.crash_signature import derive_signature, is_system_binary

_ADMIN = {"X-Admin-Key": "test-admin-key"}
_URL = "/v1/diagnostics/crash"


# A realistic MXCrashDiagnostic.jsonRepresentation, written from Apple's
# documented MXCallStackTree and MXCrashDiagnostic shapes. Thread 0 is NOT
# attributed and has its own app frame, so picking the wrong thread gives a
# different signature. The attributed thread starts in system code and
# reaches the app two levels down.
SAMPLE_PAYLOAD = {
    "version": "1.0.0",
    "callStackTree": {
        "callStackPerThread": True,
        "callStacks": [
            {
                "threadAttributed": False,
                "callStackRootFrames": [{
                    "binaryUUID": "70B89F27-1634-3580-A695-57CDB41D7743",
                    "offsetIntoBinaryTextSegment": 4128,
                    "sampleCount": 1,
                    "binaryName": "libsystem_kernel.dylib",
                    "address": 7_516_196_896,
                    "subFrames": [{
                        "binaryUUID": "9E1D2B3C-4A5F-3B6E-8C7D-0F1E2D3C4B5A",
                        "offsetIntoBinaryTextSegment": 555,
                        "sampleCount": 1,
                        "binaryName": "ShoulderSurf",
                        "address": 4_295_000_555,
                    }],
                }],
            },
            {
                "threadAttributed": True,
                "callStackRootFrames": [{
                    "binaryUUID": "2B1D4E5F-6A7B-3C8D-9E0F-1A2B3C4D5E6F",
                    "offsetIntoBinaryTextSegment": 200_340,
                    "sampleCount": 1,
                    "binaryName": "libswiftCore.dylib",
                    "address": 6_710_086_292,
                    "subFrames": [{
                        "binaryUUID": "B7C8D9E0-F1A2-3B4C-5D6E-7F8091A2B3C4",
                        "offsetIntoBinaryTextSegment": 81_234,
                        "sampleCount": 1,
                        "binaryName": "SwiftUICore",
                        "address": 6_800_081_234,
                        "subFrames": [{
                            "binaryUUID": "9E1D2B3C-4A5F-3B6E-8C7D-0F1E2D3C4B5A",
                            "offsetIntoBinaryTextSegment": 1_193_046,
                            "sampleCount": 1,
                            "binaryName": "ShoulderSurf",
                            "address": 4_296_160_342,
                            "subFrames": [{
                                "binaryUUID": "9E1D2B3C-4A5F-3B6E-8C7D-0F1E2D3C4B5A",
                                "offsetIntoBinaryTextSegment": 999,
                                "sampleCount": 1,
                                "binaryName": "ShoulderSurf",
                                "address": 4_294_968_295,
                            }],
                        }],
                    }],
                }],
            },
        ],
    },
    "diagnosticMetaData": {
        "appBuildVersion": "812",
        "appVersion": "1.19",
        "regionFormat": "US",
        "exceptionType": 1,
        "osVersion": "iPhone OS 26.1 (23B85)",
        "deviceType": "iPhone17,3",
        "signal": 11,
        "exceptionCode": 0,
        "platformArchitecture": "arm64e",
        "terminationReason": "Namespace SIGNAL, Code 11 Segmentation fault: 11",
        "virtualMemoryRegionInfo": "0 is not in any region.",
        "isTestFlightApp": False,
        "lowPowerModeEnabled": False,
    },
}


def _fields(**over):
    base = {"kind": "crash", "exception_code": None, "signal": None, "exception_type": None}
    base.update(over)
    return base


def _report(**over) -> dict:
    body = {
        "report_id": str(uuid.uuid4()).upper(),
        "kind": "crash",
        "crash_time": "2026-11-24T10:15:30Z",
        "app_version": "1.19",
        "app_build": "812",
        "os_version": "iPhone OS 26.1 (23B85)",
        "device_model": "iPhone17,3",
        "signal": "SIGSEGV",
        "exception_type": "EXC_BAD_ACCESS",
        "exception_code": "KERN_INVALID_ADDRESS",
        "termination_reason": "Namespace SIGNAL, Code 11 Segmentation fault: 11",
        "payload": copy.deepcopy(SAMPLE_PAYLOAD),
    }
    body.update(over)
    return body


def _rows(db_path, sql="SELECT * FROM crash_reports", args=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# --- signature ---------------------------------------------------------------


def test_signature_uses_attributed_thread_and_first_app_frame():
    sig = derive_signature(_fields(exception_code="KERN_INVALID_ADDRESS"), SAMPLE_PAYLOAD)
    assert sig == "KERN_INVALID_ADDRESS|ShoulderSurf+1193046"


def test_signature_code_precedence_exception_code_then_signal_then_type():
    p = SAMPLE_PAYLOAD
    assert derive_signature(_fields(exception_code="C", signal="S", exception_type="T"), p).startswith("C|")
    assert derive_signature(_fields(signal="S", exception_type="T"), p).startswith("S|")
    assert derive_signature(_fields(exception_type="T"), p).startswith("T|")


def test_signature_falls_back_to_payload_metadata_then_kind():
    # Top-level codes absent: the payload's diagnosticMetaData exceptionCode
    # (0, which is a real value and not "missing") wins.
    assert derive_signature(_fields(), SAMPLE_PAYLOAD) == "0|ShoulderSurf+1193046"
    hang = {"callStackTree": SAMPLE_PAYLOAD["callStackTree"]}
    assert derive_signature(_fields(kind="hang"), hang) == "hang|ShoulderSurf+1193046"


def test_signature_without_attributed_thread_uses_the_first_thread():
    p = copy.deepcopy(SAMPLE_PAYLOAD)
    for t in p["callStackTree"]["callStacks"]:
        t.pop("threadAttributed")
    assert derive_signature(_fields(signal="S"), p) == "S|ShoulderSurf+555"


def test_signature_all_system_frames_and_odd_shapes_never_raise():
    p = {"callStackTree": {"callStacks": [{"threadAttributed": True, "callStackRootFrames": [
        {"binaryName": "libsystem_kernel.dylib", "offsetIntoBinaryTextSegment": 7,
         "subFrames": [{"binaryName": "UIKitCore", "offsetIntoBinaryTextSegment": 8}]}]}]}}
    assert derive_signature(_fields(signal="S"), p) == "S|sys:libsystem_kernel.dylib+7"
    # The tree sent bare, without the callStackTree wrapper.
    assert derive_signature(_fields(signal="S"), SAMPLE_PAYLOAD["callStackTree"]) == "S|ShoulderSurf+1193046"
    for odd in ({}, {"callStackTree": []}, {"callStackTree": {"callStacks": "x"}},
                {"callStackTree": {"callStacks": [{"callStackRootFrames": [1, None, "f"]}]}},
                [], None, "text"):
        assert derive_signature(_fields(signal="S"), odd).startswith("S|")


def test_system_binary_classification():
    for name in ("libsystem_kernel.dylib", "libdyld.dylib", "dyld", "UIKitCore",
                 "CoreFoundation", "Foundation", "SwiftUI", "libswiftCore.dylib", None, ""):
        assert is_system_binary(name), name
    for name in ("ShoulderSurf", "Shoulder Surf", "WhisperKit"):
        assert not is_system_binary(name), name


# --- intake --------------------------------------------------------------------


# The payload text deliberately differs from what json.dumps would write:
# keys out of alphabetical order, raw non-ASCII next to an escaped one, an
# integer past 64 bits, float spellings a re-serializer rewrites (1.10, 1E+2,
# -0.0, 0.1000000000000000055511151231257827), and odd whitespace.
_PAYLOAD_TEXT = (
    '{"zeta": 1, "alpha": {"b": 2, "a": [3, 2, 1]},'
    ' "unicode": "café 東京 \U0001F600", "escaped": "caf\\u00e9 \\ud83d\\ude00",'
    ' "big": 123456789012345678901234567890, "f1": 1.10, "f2": 1E+2, "neg0": -0.0,'
    ' "f3": 0.1000000000000000055511151231257827,'
    '  "callStackTree" :{"callStacks":[{"threadAttributed":true,"callStackRootFrames":'
    '[{"binaryName":"ShoulderSurf","offsetIntoBinaryTextSegment":42}]}]} }'
)


def _raw_body(report_id: str, payload_text: str = _PAYLOAD_TEXT, extra: str = "") -> bytes:
    return (
        '{"report_id": "%s", "kind": "crash", "crash_time": null,'
        ' "app_version": "1.19", "app_build": "812", "os_version": "26.1",'
        ' "device_model": "iPhone17,3", "signal": "SIGABRT", "exception_type": null,'
        ' "exception_code": null, "termination_reason": null,%s'
        ' "payload": %s}' % (report_id, extra, payload_text)
    ).encode("utf-8")


def test_payload_is_stored_byte_for_byte_and_served_back_exactly(client, tmp_db_path):
    rid = str(uuid.uuid4())
    r = client.post(_URL, content=_raw_body(rid), headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    assert r.json() == {"stored": True, "duplicate": False}

    (row,) = _rows(tmp_db_path)
    stored = row["payload"]
    # Byte for byte against the text on the wire.
    assert stored.encode("utf-8") == _PAYLOAD_TEXT.encode("utf-8")
    # And the same object, key order included, when parsed both ways.
    pairs = lambda t: json.loads(t, object_pairs_hook=lambda kv: kv)  # noqa: E731
    assert pairs(stored) == pairs(_PAYLOAD_TEXT)
    assert json.loads(stored)["big"] == 123456789012345678901234567890
    assert row["signature"] == "SIGABRT|ShoulderSurf+42"

    # The read side splices the same text back, untouched.
    d = client.get(f"/webhooks/admin/crash-reports/{rid}", headers=_ADMIN)
    assert d.status_code == 200
    assert _PAYLOAD_TEXT.encode("utf-8") in d.content
    detail = d.json()
    assert detail["report_id"] == rid and detail["signature"] == "SIGABRT|ShoulderSurf+42"
    assert list(detail["payload"]) == [k for k, _ in pairs(_PAYLOAD_TEXT)]


def test_duplicate_report_id_is_stored_once_and_not_overwritten(client, tmp_db_path):
    first = _report(report_id=str(uuid.uuid4()).upper())
    r1 = client.post(_URL, json=first)
    assert r1.json() == {"stored": True, "duplicate": False}

    second = _report(report_id=first["report_id"].lower(), kind="hang",
                     app_build="999", payload={"different": True})
    r2 = client.post(_URL, json=second)
    assert r2.status_code == 200
    assert r2.json() == {"stored": True, "duplicate": True}

    rows = _rows(tmp_db_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == "crash" and rows[0]["app_build"] == "812"
    assert json.loads(rows[0]["payload"]) == SAMPLE_PAYLOAD


def _sized_body(total: int) -> bytes:
    """A valid report whose encoded body is exactly `total` bytes."""
    rid = str(uuid.uuid4())
    shell = _raw_body(rid, '{"pad": ""}')
    pad = total - len(shell)
    assert pad >= 0
    return _raw_body(rid, '{"pad": "%s"}' % ("x" * pad))


def test_body_at_256_kb_is_accepted_and_one_byte_over_is_413(client, tmp_db_path):
    ok = _sized_body(256 * 1024)
    assert len(ok) == 262144
    r = client.post(_URL, content=ok, headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text

    over = _sized_body(256 * 1024 + 1)
    r = client.post(_URL, content=over, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "body_too_large"
    assert r.json()["detail"]["details"]["check"] == "content_length"
    assert len(_rows(tmp_db_path)) == 1


def test_body_over_256_kb_without_content_length_is_413_on_streamed_bytes(client, tmp_db_path):
    over = _sized_body(256 * 1024 + 1)

    def chunks():  # a generator body goes out chunked, no Content-Length
        for i in range(0, len(over), 8192):
            yield over[i:i + 8192]

    r = client.post(_URL, content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["detail"]["details"]["check"] == "streamed_bytes"
    assert _rows(tmp_db_path) == []


def _all_text_cells(db_path):
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            for row in conn.execute(f'SELECT * FROM "{t}"').fetchall():
                for v in row:
                    if isinstance(v, (str, bytes)):
                        yield t, v if isinstance(v, str) else v.decode("latin-1")
    finally:
        conn.close()


def test_no_user_device_or_ip_linkage_even_with_a_bearer_token(client, tmp_db_path, free_user):
    ip = "203.0.113.77"
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()
    device_id = str(uuid.uuid4())
    body = _report(user_id=free_user["user_id"], device_id=device_id)
    headers = {**free_user["headers"], "X-Forwarded-For": ip, "X-App-ID": "shouldersurf"}
    r = client.post(_URL, json=body, headers=headers)
    assert r.status_code == 200, r.text

    (row,) = _rows(tmp_db_path)
    assert row["app_id"] == "shouldersurf"
    cols = set(row)
    for banned in ("user_id", "device_id", "ip", "ip_hash", "client_ip"):
        assert banned not in cols
    for value in row.values():
        text = str(value)
        assert free_user["user_id"] not in text
        assert device_id not in text
        assert ip not in text and ip_hash not in text
    # Nowhere in the whole database either: neither the IP nor its hash.
    for table, text in _all_text_cells(tmp_db_path):
        assert ip not in text and ip_hash not in text, table


def test_bad_report_id_kind_and_payload_are_400_with_codes(client, tmp_db_path):
    cases = [
        (_report(report_id="not-a-uuid"), "invalid_report_id"),
        (_report(report_id=None), "invalid_report_id"),
        (_report(kind="oom"), "invalid_kind"),
        (_report(payload=[1, 2]), "invalid_payload"),
        (_report(crash_time="yesterday"), "invalid_crash_time"),
        (_report(app_build=812), "invalid_field"),
    ]
    for body, code in cases:
        r = client.post(_URL, json=body)
        assert r.status_code == 400, (code, r.text)
        assert r.json()["detail"]["code"] == code
    r = client.post(_URL, content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_json"
    assert _rows(tmp_db_path) == []


def test_unknown_top_level_fields_are_accepted_and_not_stored(client, tmp_db_path):
    r = client.post(_URL, content=_raw_body(str(uuid.uuid4()), extra=' "future_field": "zzq-marker",'),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    (row,) = _rows(tmp_db_path)
    assert all("zzq-marker" not in str(v) for v in row.values())


def test_rate_limit_is_30_per_hour_per_client(client):
    headers = {"X-Forwarded-For": "198.51.100.9"}
    for _ in range(30):
        assert client.post(_URL, json=_report(), headers=headers).status_code == 200
    r = client.post(_URL, json=_report(), headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"
    assert r.json()["detail"]["details"]["retry_after"] > 60
    # Another client is unaffected.
    assert client.post(_URL, json=_report(), headers={"X-Forwarded-For": "198.51.100.10"}).status_code == 200


# --- admin read side ---------------------------------------------------------------


def _hang_payload(binary: str, offset: int) -> dict:
    return {"callStackTree": {"callStackPerThread": False, "callStacks": [{
        "threadAttributed": True, "callStackRootFrames": [{
            "binaryName": "libsystem_kernel.dylib", "offsetIntoBinaryTextSegment": 1,
            "subFrames": [{"binaryName": binary, "offsetIntoBinaryTextSegment": offset}]}]}]}}


def test_summary_groups_by_signature(client, tmp_db_path):
    a1 = _report(app_build="812", device_model="iPhone17,3", os_version="26.1")
    a2 = _report(app_build="813", device_model="iPhone16,2", os_version="26.1")
    a3 = _report(app_build="813", device_model="iPhone17,3", os_version="26.0")
    h1 = _report(kind="hang", signal=None, exception_type=None, exception_code=None,
                 app_build="813", payload=_hang_payload("ShoulderSurf", 7777))
    for body in (a1, a2, a3, h1):
        assert client.post(_URL, json=body).json()["duplicate"] is False

    # An old row outside a 30 day window, same signature as the crash group.
    conn = sqlite3.connect(tmp_db_path)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    conn.execute("UPDATE crash_reports SET received_at = ? WHERE report_id = ?",
                 (old, a1["report_id"].lower()))
    conn.commit()
    conn.close()

    assert client.get("/webhooks/admin/crash-reports/summary",
                      headers={"X-Admin-Key": "wrong"}).status_code == 403

    s = client.get("/webhooks/admin/crash-reports/summary?days=30", headers=_ADMIN).json()
    assert s["total"] == 3
    crash_sig = "KERN_INVALID_ADDRESS|ShoulderSurf+1193046"
    hang_sig = "hang|ShoulderSurf+7777"
    by = {g["signature"]: g for g in s["groups"]}
    assert set(by) == {crash_sig, hang_sig}
    assert [g["signature"] for g in s["groups"]] == [crash_sig, hang_sig]  # most first
    c = by[crash_sig]
    assert c["count"] == 2
    assert c["kinds"] == ["crash"]
    assert c["app_builds"] == ["813"]
    assert c["device_models"] == ["iPhone16,2", "iPhone17,3"]
    assert c["os_versions"] == ["26.0", "26.1"]
    assert c["example_report_id"] in {a2["report_id"].lower(), a3["report_id"].lower()}
    assert c["first_seen"] <= c["last_seen"]
    assert by[hang_sig]["count"] == 1 and by[hang_sig]["kinds"] == ["hang"]

    wide = client.get("/webhooks/admin/crash-reports/summary?days=60", headers=_ADMIN).json()
    wc = {g["signature"]: g for g in wide["groups"]}[crash_sig]
    assert wc["count"] == 3 and wc["app_builds"] == ["812", "813"]
    assert wc["first_seen"] == old

    only_hang = client.get("/webhooks/admin/crash-reports/summary?kind=hang", headers=_ADMIN).json()
    assert [g["signature"] for g in only_hang["groups"]] == [hang_sig]
    b812 = client.get("/webhooks/admin/crash-reports/summary?days=60&app_build=812", headers=_ADMIN).json()
    assert b812["total"] == 1
    assert b812["groups"][0]["example_report_id"] == a1["report_id"].lower()


def test_list_omits_payload_and_detail_404s(client, tmp_db_path):
    a = _report(app_build="812")
    b = _report(app_build="813")
    for body in (a, b):
        client.post(_URL, json=body)
    lst = client.get("/webhooks/admin/crash-reports?app_build=813", headers=_ADMIN).json()
    assert [r["report_id"] for r in lst["reports"]] == [b["report_id"].lower()]
    assert "payload" not in lst["reports"][0]
    sig = lst["reports"][0]["signature"]
    # The signature carries "+", so it must go out URL encoded.
    both = client.get("/webhooks/admin/crash-reports", params={"signature": sig},
                      headers=_ADMIN).json()["reports"]
    assert len(both) == 2
    assert len(client.get("/webhooks/admin/crash-reports", params={"signature": sig, "limit": 1},
                          headers=_ADMIN).json()["reports"]) == 1
    assert client.get(f"/webhooks/admin/crash-reports/{uuid.uuid4()}", headers=_ADMIN).status_code == 404
    assert client.get("/webhooks/admin/crash-reports", headers={"X-Admin-Key": "x"}).status_code == 403


def test_crash_reports_purge_at_180_days(tmp_path):
    from app.database import init_db
    from app.services.retention import purge_expired
    import aiosqlite

    db_path = str(tmp_path / "crash.db")
    asyncio.run(init_db(f"sqlite+aiosqlite:///{db_path}"))
    conn = sqlite3.connect(db_path)
    for rid, days in (("old", 181), ("kept", 179)):
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn.execute("INSERT INTO crash_reports (report_id, received_at, kind, signature, payload)"
                     " VALUES (?, ?, 'crash', 's', '{}')", (rid, stamp))
    conn.commit()
    conn.close()

    async def _run():
        async with aiosqlite.connect(db_path) as db:
            return await purge_expired(db)

    asyncio.run(_run())
    assert [r["report_id"] for r in _rows(db_path)] == ["kept"]


def test_crash_route_is_kept_out_of_the_live_log():
    from app.middleware import request_logging
    assert "/v1/diagnostics/crash" in request_logging._SKIP_PATHS


def test_swiftui_engine_frames_are_system():
    from app.services.crash_signature import is_system_binary
    for name in ("AttributeGraph", "RenderBox", "FoundationModels"):
        assert is_system_binary(name), name
    assert not is_system_binary("Shoulder Surf")
    assert not is_system_binary("Shoulder Surf.debug.dylib")
