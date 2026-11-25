"""MetricKit crash and hang intake from Shoulder Surf (2026-11-24).

Wire shape (contract proposed by SS, field names kept exactly):

  POST /v1/diagnostics/crash          no auth, like /v1/events/ping
  {
    "report_id": UUID,                 client generated, the dedupe key
    "kind": "crash" | "hang",
    "crash_time": ISO8601 | null,
    "app_version": str, "app_build": str, "os_version": str,
    "device_model": str,
    "signal": str | null, "exception_type": str | null,
    "exception_code": str | null, "termination_reason": str | null,
    "payload": object                  MXCrashDiagnostic / MXHangDiagnostic
                                       jsonRepresentation
  }
  -> 200 {"stored": true, "duplicate": false}   first time a report_id is seen
  -> 200 {"stored": true, "duplicate": true}    a repeat; nothing overwritten
  -> 400 {"detail": {"code": ..., "message": ...}}
         codes: invalid_json, invalid_report_id, invalid_kind,
                invalid_crash_time, invalid_field, invalid_payload
  -> 413 {"detail": {"code": "body_too_large", "details": {"check": ...}}}
         check is "content_length" when the declared Content-Length is over
         the cap (nothing is read), or "streamed_bytes" when the bytes
         actually received pass it (a missing or lying header)
  -> 429 {"detail": {"code": "rate_limited", ...}}  30 per hour per client

Privacy, a hard rule: the App Privacy label says "Crash Data, not linked to
you". So nothing here reads the Authorization header (a Bearer token sent
anyway is ignored), and the row holds no user_id, device id, IP or IP hash.
The client IP is hashed only to key the in-memory rate limiter, the same
way /v1/events/ping keys it, and that hash is never written anywhere.
Unknown top-level fields are accepted and DROPPED, not stored: an extras
column would be exactly where a client could start sending a device id
without anyone noticing. Their key names (never values) are logged once
per process, the same allow-then-report rule as the ping route.

The payload is stored as the exact text it had in the request body. The
body is parsed once to validate it, then the top-level object is walked
with the stdlib scanner to find the byte span of the `payload` member, and
that span is what lands in the column. So key order, unicode escapes,
large integers and float spellings (1.10, 1E+2) all survive exactly, which
a parse and re-serialize would not guarantee for floats. The detail route
splices the same text back into its response.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response

from app.database import get_db
from app.routers.telemetry import _client_ip, _ip_hash
from app.routers.webhooks import _verify_admin
from app.services.crash_signature import derive_signature

logger = logging.getLogger(__name__)

router = APIRouter()
admin_router = APIRouter()

MAX_BODY_BYTES = 256 * 1024
CRASH_REPORTS_PER_HOUR = 30
_RATE_WINDOW_SECONDS = 3600.0

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_KINDS = ("crash", "hang")

# Scalar string fields and their length caps. termination_reason is long on
# purpose: Apple's reasons run to a few hundred characters.
_STRING_FIELDS = {
    "app_version": 64, "app_build": 64, "os_version": 128,
    "device_model": 64, "signal": 128, "exception_type": 128,
    "exception_code": 128, "termination_reason": 2048,
}
_KNOWN = frozenset({"report_id", "kind", "crash_time", "payload", *_STRING_FIELDS})

_UNKNOWN_KEYS_SEEN: set[tuple] = set()

_JSON_WS = " \t\n\r"


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _too_large(check: str, size: int) -> HTTPException:
    return HTTPException(status_code=413, detail={
        "code": "body_too_large",
        "message": f"Crash report body is over {MAX_BODY_BYTES} bytes",
        "details": {"check": check, "limit_bytes": MAX_BODY_BYTES, "bytes": size},
    })


async def _read_capped_body(request: Request) -> bytes:
    """Read the raw body, refusing past the cap BEFORE anything is parsed.

    The declared Content-Length is checked first because it costs nothing,
    but it is a claim by the sender, so the bytes actually streamed are the
    real bound (the shape of shares._read_archive_capped)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large("content_length", int(declared))
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            raise _too_large("streamed_bytes", len(buf))
    return bytes(buf)


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in _JSON_WS:
        i += 1
    return i


def _member_spans(text: str) -> dict[str, tuple[int, int]]:
    """Character span of each top-level member's VALUE in a JSON object.

    Only called after json.loads accepted the same text, so the shape is
    known good. A repeated key keeps its last span, matching json.loads."""
    decoder = json.JSONDecoder()
    spans: dict[str, tuple[int, int]] = {}
    i = _skip_ws(text, 0)
    if text[i] != "{":
        raise ValueError("not an object")
    i = _skip_ws(text, i + 1)
    if text[i] == "}":
        return spans
    while True:
        if text[i] != '"':
            raise ValueError("expected a key")
        key, i = json.decoder.scanstring(text, i + 1)
        i = _skip_ws(text, i)
        if text[i] != ":":
            raise ValueError("expected a colon")
        start = _skip_ws(text, i + 1)
        _, end = decoder.raw_decode(text, start)
        spans[key] = (start, end)
        i = _skip_ws(text, end)
        if text[i] == ",":
            i = _skip_ws(text, i + 1)
            continue
        if text[i] == "}":
            return spans
        raise ValueError("expected a comma or a closing brace")


def _parse(raw: bytes) -> tuple[dict, str]:
    """Return (parsed body, exact payload text)."""
    try:
        text = raw.decode("utf-8")
        body = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        raise _bad("invalid_json", "Body must be a UTF-8 JSON object")
    if not isinstance(body, dict):
        raise _bad("invalid_json", "Body must be a UTF-8 JSON object")
    if not isinstance(body.get("payload"), dict):
        raise _bad("invalid_payload", "payload must be a JSON object")
    start, end = _member_spans(text)["payload"]
    return body, text[start:end]


def _validate(body: dict) -> dict:
    report_id = body.get("report_id")
    if not isinstance(report_id, str) or not _UUID_RE.match(report_id):
        raise _bad("invalid_report_id", "report_id must be a UUID")
    kind = body.get("kind")
    if kind not in _KINDS:
        raise _bad("invalid_kind", "kind must be crash or hang")

    crash_time = body.get("crash_time")
    if crash_time is not None:
        if not isinstance(crash_time, str) or len(crash_time) > 64:
            raise _bad("invalid_crash_time", "crash_time must be ISO8601 or null")
        try:
            datetime.fromisoformat(crash_time)
        except ValueError:
            raise _bad("invalid_crash_time", "crash_time must be ISO8601 or null")

    row = {
        # Swift's uuidString is upper case; one spelling per report so a
        # case difference can never defeat the dedupe.
        "report_id": report_id.lower(),
        "kind": kind,
        "crash_time": crash_time,
    }
    for field, cap in _STRING_FIELDS.items():
        value = body.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > cap):
            raise _bad("invalid_field", f"{field} must be a string of at most {cap} characters or null")
        row[field] = value
    return row


@router.post("/diagnostics/crash")
async def intake_crash(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Store one MetricKit crash or hang report. No auth by design."""
    ip_h = _ip_hash(_client_ip(request))
    if ip_h:
        allowed, retry_after = request.app.state.rate_limiter.check(
            f"crash_hourly:{ip_h}", CRASH_REPORTS_PER_HOUR, window=_RATE_WINDOW_SECONDS)
        if not allowed:
            raise HTTPException(status_code=429, detail={
                "code": "rate_limited",
                "message": f"Crash report rate limit hit; retry in {retry_after}s",
                "details": {"retry_after": retry_after},
            })

    raw = await _read_capped_body(request)
    body, payload_text = _parse(raw)
    row = _validate(body)
    app_id = getattr(request.state, "app_id", "unknown")

    extra = sorted(k for k in body if k not in _KNOWN)
    if extra:
        key = (app_id, tuple(extra))
        if key not in _UNKNOWN_KEYS_SEEN:
            _UNKNOWN_KEYS_SEEN.add(key)
            logger.warning("crash_report_unknown_fields app=%s keys=%s (accepted, NOT stored)",
                           app_id, ",".join(extra))

    signature = derive_signature(body, body["payload"])
    cur = await db.execute(
        """INSERT INTO crash_reports
               (report_id, received_at, app_id, kind, crash_time, app_version,
                app_build, os_version, device_model, signal, exception_type,
                exception_code, termination_reason, signature, payload)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(report_id) DO NOTHING""",
        (row["report_id"], datetime.now(timezone.utc).isoformat(), app_id,
         row["kind"], row["crash_time"], row["app_version"], row["app_build"],
         row["os_version"], row["device_model"], row["signal"],
         row["exception_type"], row["exception_code"],
         row["termination_reason"], signature, payload_text),
    )
    await db.commit()
    duplicate = cur.rowcount != 1
    if not duplicate:
        logger.info("crash_report_stored app=%s kind=%s build=%s signature=%s",
                    app_id, row["kind"], row["app_build"], signature)
    return {"stored": True, "duplicate": duplicate}


# --- read side, admin key ---------------------------------------------------

_ROW_COLUMNS = ("report_id", "received_at", "app_id", "kind", "crash_time",
                "app_version", "app_build", "os_version", "device_model",
                "signal", "exception_type", "exception_code",
                "termination_reason", "signature")


@admin_router.get("/admin/crash-reports/summary")
async def crash_summary(
    request: Request,
    days: int = Query(30, ge=1, le=180),
    app_build: str | None = None,
    kind: str | None = None,
    app_id: str | None = None,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Reports grouped by signature, most frequent first. The window and
    first_seen/last_seen are on received_at (crash_time can be null, and
    MetricKit delivers up to a day late)."""
    _verify_admin(request, x_admin_key)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    where, args = ["received_at >= ?"], [since]
    for col, val in (("app_build", app_build), ("kind", kind), ("app_id", app_id)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    filt = " AND ".join(where)
    # json_group_array, not group_concat: group_concat(DISTINCT x) only
    # takes the default comma separator, and device models contain commas
    # ("iPhone17,3"). The example is the newest report inside the same
    # window and filters.
    rows = await (await db.execute(
        f"""SELECT signature, COUNT(*) AS n,
                   json_group_array(DISTINCT kind) AS kinds,
                   json_group_array(DISTINCT app_build) AS builds,
                   json_group_array(DISTINCT device_model) AS models,
                   json_group_array(DISTINCT os_version) AS oses,
                   MIN(received_at) AS first_seen, MAX(received_at) AS last_seen,
                   (SELECT report_id FROM crash_reports
                     WHERE signature = c.signature AND {filt}
                     ORDER BY received_at DESC, report_id LIMIT 1) AS example
            FROM crash_reports c
            WHERE {filt}
            GROUP BY signature
            ORDER BY n DESC, last_seen DESC, signature""",
        [*args, *args],
    )).fetchall()

    def values(v):
        return sorted(x for x in json.loads(v or "[]") if x is not None)

    groups = [{
        "signature": r["signature"],
        "count": r["n"],
        "kinds": values(r["kinds"]),
        "app_builds": values(r["builds"]),
        "device_models": values(r["models"]),
        "os_versions": values(r["oses"]),
        "first_seen": r["first_seen"],
        "last_seen": r["last_seen"],
        "example_report_id": r["example"],
    } for r in rows]
    return {"days": days, "total": sum(g["count"] for g in groups), "groups": groups}


@admin_router.get("/admin/crash-reports")
async def crash_list(
    request: Request,
    signature: str | None = None,
    app_build: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Newest first, without payloads."""
    _verify_admin(request, x_admin_key)
    where, args = [], []
    for col, val in (("signature", signature), ("app_build", app_build)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = await (await db.execute(
        f"SELECT {', '.join(_ROW_COLUMNS)} FROM crash_reports {clause} "
        "ORDER BY received_at DESC LIMIT ?",
        [*args, limit],
    )).fetchall()
    return {"reports": [dict(r) for r in rows]}


@admin_router.get("/admin/crash-reports/{report_id}")
async def crash_detail(
    report_id: str,
    request: Request,
    x_admin_key: str = Header(...),
    db: aiosqlite.Connection = Depends(get_db),
) -> Response:
    """One row with its full payload. The payload is spliced in as the exact
    text that was stored, not re-serialized, so what an operator reads is
    byte for byte what the device sent."""
    _verify_admin(request, x_admin_key)
    row = await (await db.execute(
        f"SELECT {', '.join(_ROW_COLUMNS)}, payload FROM crash_reports WHERE report_id = ?",
        (report_id.lower(),),
    )).fetchone()
    if row is None:
        raise _bad("not_found", "No crash report with that report_id", status=404)
    head = json.dumps({c: row[c] for c in _ROW_COLUMNS}, ensure_ascii=False)
    body = head[:-1] + ', "payload": ' + row["payload"] + "}"
    return Response(content=body.encode("utf-8"), media_type="application/json")
