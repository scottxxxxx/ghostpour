"""Document generation phase 2a: staging store, serve endpoint, gate,
adapter arming, artifact collection, response wire field.

Design: docs/design/documents-phase2-returned-files.md. Ships dark
(documents.generation.enabled false); allowed_users (shared with phase 1)
is the e2e lane.
"""

import json

import pytest

from app.services.document_generation import (
    _walk_file_ids,
    generation_gate,
    load_generation_config,
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _configs(gen=None, allowed=None):
    docs = {"enabled": False, "allowed_users": allowed or []}
    if gen is not None:
        docs["generation"] = gen
    return {"client-config": {"documents": docs}}


# --- config + gate ---

def test_generation_defaults_ship_dark():
    cfg = load_generation_config({})
    assert cfg["enabled"] is False
    assert cfg["min_tier"] == "pro"
    assert len(cfg["formats"]) == 4  # all four launch formats (§9 decision 2)
    assert cfg["max_files_out"] == 2 and cfg["max_file_out_mb"] == 25


def test_bundled_config_ships_generation_dark():
    for f in ("client-config.json", "client-config.es.json", "client-config.ja.json"):
        gen = json.load(open(f"config/remote/{f}"))["documents"]["generation"]
        assert gen["enabled"] is False and gen["min_tier"] == "pro"
        assert len(gen["formats"]) == 4


def _gate(**over):
    kw = dict(
        remote_configs=_configs(gen={"enabled": True, "min_tier": "pro"}),
        tier_name="pro", managed_routing=True, provider="anthropic",
        prompt_mode="ProjectChat", user_identity={"u1"},
    )
    kw.update(over)
    return generation_gate(**kw)


def test_gate_matrix():
    assert _gate() is True
    assert _gate(prompt_mode="PostMeetingChat") is True
    # every mechanical requirement individually closes the gate
    assert _gate(prompt_mode=None) is False
    assert _gate(prompt_mode="InterviewScorecard") is False
    assert _gate(managed_routing=False) is False
    assert _gate(provider="openrouter") is False
    # enabled+tier path
    assert _gate(tier_name="plus") is False
    assert _gate(remote_configs=_configs(gen={"enabled": False})) is False
    # allowed_users overrides enabled AND tier (e2e lane, shared w/ phase 1)
    assert _gate(
        remote_configs=_configs(gen={"enabled": False}, allowed=["scott@x.com"]),
        tier_name="plus", user_identity={"scott@x.com"},
    ) is True


# --- adapter arming ---

def _adapter():
    from app.services.providers.anthropic import AnthropicAdapter
    return AnthropicAdapter(
        api_key="test", base_url="https://example.invalid/v1/messages",
        auth_header="x-api-key", auth_prefix="",
    )


def _body(generation):
    from app.models.chat import ChatRequest
    return ChatRequest(
        provider="anthropic", model="claude-sonnet-4-6",
        system_prompt="sys", user_content="make me a spreadsheet",
        generation=generation, max_tokens=4096,
    )


def test_adapter_arms_generation():
    api_body, headers = _adapter()._build_body(_body(True))
    skills = {s["skill_id"] for s in api_body["container"]["skills"]}
    assert skills == {"xlsx", "pptx", "docx", "pdf"}
    assert any(t["type"] == "code_execution_20260521" for t in api_body["tools"])
    assert api_body["max_tokens"] >= 16000
    # spike-mandated: cache breakpoint on the user content ($1.04 -> $0.33)
    text_parts = [p for p in api_body["messages"][0]["content"] if p["type"] == "text"]
    assert text_parts[-1]["cache_control"] == {"type": "ephemeral"}
    assert "code-execution-2025-08-25" in headers["anthropic-beta"]
    assert "skills-2025-10-02" in headers["anthropic-beta"]


def test_adapter_unarmed_is_untouched():
    api_body, headers = _adapter()._build_body(_body(False))
    assert "container" not in api_body
    assert "tools" not in api_body
    assert api_body["max_tokens"] == 4096
    assert "anthropic-beta" not in headers or "skills" not in headers.get("anthropic-beta", "")


# --- artifact collection ---

def test_walk_file_ids_finds_and_dedups():
    raw = json.dumps({"content": [
        {"type": "text", "text": "done"},
        {"type": "bash_code_execution_tool_result", "content": {
            "type": "bash_code_execution_result",
            "content": [{"type": "bash_code_execution_output", "file_id": "file_A"}]}},
        {"type": "bash_code_execution_tool_result", "content": {
            "type": "bash_code_execution_result",
            "content": [{"type": "bash_code_execution_output", "file_id": "file_A"},
                        {"type": "bash_code_execution_output", "file_id": "file_B"}]}},
    ]})
    assert _walk_file_ids(raw) == ["file_A", "file_B"]
    assert _walk_file_ids("not json") == []
    assert _walk_file_ids(json.dumps({"content": [{"type": "text", "text": "hi"}]})) == []


@pytest.mark.asyncio
async def test_collect_downloads_validates_and_stages(tmp_path, monkeypatch):
    import aiosqlite
    from app.services import generated_files as staging
    from app.services.document_generation import collect_generated_files

    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path / "gen")

    class _Resp:
        def __init__(self, status, payload=None, content=b""):
            self.status_code, self._p, self.content = status, payload, content
        def json(self):
            return self._p

    calls = {}
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            calls.setdefault("urls", []).append(url)
            if url.endswith("/content"):
                return _Resp(200, content=b"PK\x03\x04 fake xlsx bytes")
            if url.endswith("file_big"):
                return _Resp(200, {"filename": "huge.xlsx", "mime_type": XLSX,
                                   "size_bytes": 99 * 1024 * 1024})
            if url.endswith("file_txt"):
                return _Resp(200, {"filename": "notes.txt", "mime_type": "text/plain",
                                   "size_bytes": 10})
            return _Resp(200, {"filename": "tracker.xlsx", "mime_type": XLSX,
                               "size_bytes": 20})
    import app.services.document_generation as dg
    monkeypatch.setattr(dg.httpx, "AsyncClient", _Client)

    raw = json.dumps({"content": [
        {"type": "bash_code_execution_tool_result", "content": {
            "content": [{"type": "bash_code_execution_output", "file_id": "file_ok"},
                        {"type": "bash_code_execution_output", "file_id": "file_big"},
                        {"type": "bash_code_execution_output", "file_id": "file_txt"}]}},
    ]})

    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""CREATE TABLE generated_files (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, app_id TEXT,
            name TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            storage_path TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")
        out = await collect_generated_files(
            db, raw_response_json=raw, api_key="k",
            remote_configs=_configs(gen={"enabled": True}),
            user_id="u1", app_id="shouldersurf",
        )
    # max_files_out=2 truncates to [file_ok, file_big]; big skipped on size;
    # only the valid xlsx stages
    assert len(out) == 1
    g = out[0]
    assert g["name"] == "tracker.xlsx" and g["media_type"] == XLSX
    assert g["url"].startswith("/v1/generated-files/gpf_")
    assert g["size_bytes"] == len(b"PK\x03\x04 fake xlsx bytes")
    import hashlib
    assert g["sha256"] == hashlib.sha256(b"PK\x03\x04 fake xlsx bytes").hexdigest()


# --- staging store semantics ---

@pytest.mark.asyncio
async def test_staging_expiry_ownership_and_cap(tmp_path, monkeypatch):
    import aiosqlite
    from app.services import generated_files as staging

    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path / "gen")
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""CREATE TABLE generated_files (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, app_id TEXT,
            name TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            storage_path TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")

        row = await staging.stage(db, user_id="u1", app_id="ss",
                                  name="a.xlsx", media_type=XLSX, content=b"x" * 100)
        assert row and row["expires_at"] > row["url"]  # sanity: fields present
        # owner fetch works; other user 404-equivalent
        assert await staging.fetch(db, row["file_id"], "u1") is not None
        assert await staging.fetch(db, row["file_id"], "someone-else") is None
        # live cap: a stage that would exceed 50MB is refused, not an error
        monkeypatch.setattr(staging, "PER_USER_LIVE_CAP_BYTES", 150)
        assert await staging.stage(db, user_id="u1", app_id="ss",
                                   name="b.xlsx", media_type=XLSX, content=b"y" * 100) is None
        # expiry: force-expire then purge deletes row + bytes
        await db.execute("UPDATE generated_files SET expires_at = '2000-01-01T00:00:00+00:00'")
        await db.commit()
        assert await staging.fetch(db, row["file_id"], "u1") is None
        n = await staging.purge_expired(db)
        assert n == 1
        left = await (await db.execute("SELECT COUNT(*) AS n FROM generated_files")).fetchone()
        assert left["n"] == 0


# --- serve endpoint (via app test client) ---

def test_serve_endpoint_auth_ownership_expiry(client, pro_user, tmp_db_path, tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timedelta, timezone

    blob = tmp_path / "gpf_test"
    blob.write_bytes(b"PK\x03\x04 artifact")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    con = sqlite3.connect(tmp_db_path)
    uid = pro_user["user_id"] if "user_id" in pro_user else None
    if uid is None:
        row = con.execute("SELECT id FROM users WHERE email LIKE 'test-pro-user%'").fetchone()
        uid = row[0]
    con.execute("INSERT INTO generated_files VALUES (?,?,?,?,?,?,?,?,?)",
                ("gpf_live", uid, "shouldersurf", "t.xlsx", XLSX, 11, str(blob), future, future))
    con.execute("INSERT INTO generated_files VALUES (?,?,?,?,?,?,?,?,?)",
                ("gpf_dead", uid, "shouldersurf", "t.xlsx", XLSX, 11, str(blob), past, past))
    con.commit(); con.close()

    h = pro_user["headers"]
    r = client.get("/v1/generated-files/gpf_live", headers=h)
    assert r.status_code == 200
    assert r.content == b"PK\x03\x04 artifact"
    assert "no-store" in r.headers.get("cache-control", "")
    assert client.get("/v1/generated-files/gpf_dead", headers=h).status_code == 404
    assert client.get("/v1/generated-files/gpf_missing", headers=h).status_code == 404
    assert client.get("/v1/generated-files/gpf_live").status_code in (401, 403, 422)  # no auth


# --- generation reliability: per-leg timeout + no OR fallback ---

@pytest.mark.asyncio
@pytest.mark.parametrize("generation,expected_timeout", [(True, 400.0), (False, None)])
async def test_generation_leg_gets_extended_timeout(monkeypatch, generation, expected_timeout):
    adapter = _adapter()
    seen = {}

    async def fake_post(url, body, headers, timeout=None):
        seen["timeout"] = timeout
        return 200, {"content": [{"type": "text", "text": "ok"}],
                     "stop_reason": "end_turn", "usage": {}}, "", ""
    monkeypatch.setattr(adapter, "_post", fake_post)

    await adapter.send_request(_body(generation))
    assert seen["timeout"] == expected_timeout


@pytest.mark.asyncio
async def test_generation_turn_never_falls_back_to_or(monkeypatch):
    import httpx as _httpx
    from unittest.mock import AsyncMock, MagicMock
    from app.services.anthropic_or_fallback import route_with_fallback

    router = MagicMock()
    router.route = AsyncMock(side_effect=_httpx.ReadTimeout("leg timed out"))
    settings = MagicMock()

    with pytest.raises(_httpx.ReadTimeout):
        await route_with_fallback(router, _body(True), db=None, settings=settings)
    # one attempt, no OR retry — a fallback would mean a second route() call
    assert router.route.await_count == 1


@pytest.mark.asyncio
async def test_non_generation_turn_still_falls_back(monkeypatch):
    import httpx as _httpx
    from unittest.mock import AsyncMock, MagicMock
    import app.services.anthropic_or_fallback as fb

    ok = MagicMock(name="or_response")
    router = MagicMock()
    router.route = AsyncMock(side_effect=[_httpx.ReadTimeout("boom"), ok])
    monkeypatch.setattr(fb, "_alert_on_fallback", AsyncMock())

    out = await fb.route_with_fallback(router, _body(False), db=None, settings=MagicMock())
    assert out is ok
    assert router.route.await_count == 2


# --- chat bubble = closing summary, not working transcript ---

@pytest.mark.asyncio
@pytest.mark.parametrize("generation,expected_text", [
    (True, "Done — your tracker is attached."),                 # last block only
    (False, "Let me think.\nDone — your tracker is attached."),  # unchanged join
])
async def test_generation_bubble_is_closing_summary(monkeypatch, generation, expected_text):
    adapter = _adapter()

    async def fake_post(url, body, headers, timeout=None):
        return 200, {"content": [
            {"type": "text", "text": "Let me think."},
            {"type": "server_tool_use", "id": "t1", "name": "bash_code_execution", "input": {}},
            {"type": "bash_code_execution_tool_result", "content": {}},
            {"type": "text", "text": "Done — your tracker is attached."},
        ], "stop_reason": "end_turn", "usage": {}}, "", ""
    monkeypatch.setattr(adapter, "_post", fake_post)

    resp = await adapter.send_request(_body(generation))
    assert resp.text == expected_text


def test_raw_provider_payloads_never_reach_the_wire(client, free_user, mock_provider):
    from tests.conftest import chat_request
    r = client.post("/v1/chat", json=chat_request(), headers=free_user["headers"])
    body = r.json()
    assert "raw_request_json" not in body
    assert "raw_response_json" not in body
    assert "text" in body and "model" in body  # normal shape intact


# --- confirmation envelope (handoff Part 1) ---

def test_confirmation_defaults_dark_but_bundles_live_and_agree():
    # Code DEFAULTS stay dark (a missing/stale config must never enable the
    # flow); the BUNDLES ship enabled since 2026-07-11 — while
    # generation.enabled is false this reaches only the allowed_users lane
    # (the gate runs before the confirmation logic).
    from app.services.document_generation import load_generation_config
    conf = load_generation_config({})["confirmation"]
    assert conf["enabled"] is False
    assert conf["expected_seconds"] == 150 and conf["poll_after_seconds"] == 5
    assert set(conf["format_nouns"]) == {"xlsx", "docx", "pptx", "pdf"}
    # back to dark 2026-07-12: the envelope reached Scott's device before the
    # SS client could render it ("Failed to parse CloudZap response") — flips
    # on again when their offer-rendering build lands
    for f in ("client-config.json", "client-config.es.json", "client-config.ja.json"):
        c = json.load(open(f"config/remote/{f}"))["documents"]["generation"]["confirmation"]
        assert c["enabled"] is False
        assert "{format}" in c["offer_text"]
        assert set(c["format_nouns"]) == {"xlsx", "docx", "pptx", "pdf"}


def test_locale_variant_supplies_envelope_text():
    from app.services.document_generation import load_generation_config
    configs = {
        "client-config": {"documents": {"generation": {"confirmation": {"offer_text": "EN {format}"}}}},
        "client-config.es": {"documents": {"generation": {"confirmation": {"offer_text": "ES {format}"}}}},
    }
    assert load_generation_config(configs)["confirmation"]["offer_text"] == "EN {format}"
    assert load_generation_config(configs, locale="es")["confirmation"]["offer_text"] == "ES {format}"
    # unknown locale falls back to base
    assert load_generation_config(configs, locale="fr")["confirmation"]["offer_text"] == "EN {format}"


def test_build_offer_envelope_shape():
    from app.services.document_generation import _CONFIRMATION_DEFAULTS, build_offer_envelope
    env = build_offer_envelope(_CONFIRMATION_DEFAULTS, "docx")
    fs = env["feature_state"]
    assert fs["feature"] == "document_generation"
    assert fs["state"] == "confirmation_required"
    cta = fs["cta"]
    assert cta["kind"] == "generation_offer" and cta["action"] == "confirm_generation"
    assert "a Word document" in cta["text"]
    assert cta["details"] == {"expected_format": "docx", "expected_seconds": 150, "gist": ""}
    # unknown/None format degrades to the default noun path, never a KeyError
    assert build_offer_envelope(_CONFIRMATION_DEFAULTS, None)["feature_state"]["cta"]["details"]["expected_format"] == "xlsx"


@pytest.mark.asyncio
async def test_classifier_fail_open_and_strict_parse():
    from unittest.mock import AsyncMock, MagicMock
    from app.services.document_generation import classify_generation_intent

    # provider error -> None (fail-open)
    router = MagicMock()
    router.route = AsyncMock(side_effect=RuntimeError("boom"))
    assert await classify_generation_intent(router, "make me a spreadsheet") is None

    # junk text -> None
    router.route = AsyncMock(return_value=MagicMock(text="I think probably yes?"))
    assert await classify_generation_intent(router, "make me a spreadsheet") is None

    # valid YES with prose wrapping -> parsed, format normalized
    router.route = AsyncMock(return_value=MagicMock(
        text='Sure: {"file_request": true, "format": "docx"} done'))
    out = await classify_generation_intent(router, "write this up as a word doc")
    assert out == {"file_request": True, "format": "docx", "gist": ""}

    # valid NO -> file_request False
    router.route = AsyncMock(return_value=MagicMock(
        text='{"file_request": false, "format": null}'))
    out = await classify_generation_intent(router, "what did we decide?")
    assert out == {"file_request": False, "format": None, "gist": ""}

    # bogus format value normalizes to None rather than leaking to the wire
    router.route = AsyncMock(return_value=MagicMock(
        text='{"file_request": true, "format": "exe"}'))
    out = await classify_generation_intent(router, "make me a file")
    assert out == {"file_request": True, "format": None, "gist": ""}


@pytest.mark.asyncio
async def test_classifier_meters_via_on_subcall():
    from unittest.mock import AsyncMock, MagicMock
    from app.services.document_generation import classify_generation_intent

    router = MagicMock()
    router.route = AsyncMock(return_value=MagicMock(
        text='{"file_request": true, "format": "xlsx"}'))
    seen = {}
    async def subcall(creq, cresp, cms):
        seen["call_type"] = creq.get_meta("call_type")
        seen["model"] = creq.model
    out = await classify_generation_intent(router, "build a tracker", on_subcall=subcall)
    assert out["file_request"] is True
    assert seen["call_type"] == "generation_intent"
    assert seen["model"].startswith("claude-haiku")


# --- transport Phase A (e2e through the app) ---

def _enable_confirmed_generation(client):
    docs = client.app.state.remote_configs["client-config"].setdefault("documents", {})
    docs["generation"] = {"enabled": True, "min_tier": "free",
                          "confirmation": {"enabled": True, "expected_seconds": 150}}


def test_confirmed_turn_rides_sse_and_records_rescue_row(client, free_user, mock_provider, tmp_db_path):
    import json as _json
    import sqlite3
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"generation_confirmed": True, "generation_id": "gen-sse-e2e"},
        user_content="Build me a tracking spreadsheet",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: generation_started" in r.text
    assert "event: generation_result" in r.text
    started = _json.loads(
        r.text.split("event: generation_started\ndata: ")[1].split("\n")[0])
    assert started["expected_seconds"] == 150
    result = _json.loads(
        r.text.split("event: generation_result\ndata: ")[1].split("\n")[0])
    assert result["text"]                       # the normal JSON body, verbatim
    assert "raw_request_json" not in result     # wire-slim holds on SSE too

    con = sqlite3.connect(tmp_db_path)
    row = con.execute("SELECT status, user_id FROM generations "
                      "WHERE generation_id='gen-sse-e2e'").fetchone()
    con.close()
    assert row is not None and row[0] == "done"


def test_unconfirmed_turn_fails_open_to_normal_json(client, free_user, mock_provider):
    from tests.conftest import chat_request
    _enable_confirmed_generation(client)
    # confirmation live + no confirmed flag: the classifier runs against the
    # mock provider, whose reply parses as junk -> fail-open -> normal chat,
    # NOT armed, NOT an envelope, NOT SSE.
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="Build me a tracking spreadsheet",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert "feature_state" not in body
    assert body.get("generated_files") is None or "generated_files" not in body


# --- docx Word-compat rebuild (backstop) ---

def _make_source_docx():
    import io
    import docx
    d = docx.Document()
    d.add_heading("Onboarding Guide", level=1)
    p = d.add_paragraph()
    r = p.add_run("Welcome ")
    r2 = p.add_run("aboard")
    r2.bold = True
    d.add_paragraph("First item", style="List Bullet")
    d.add_paragraph("Second item", style="List Bullet")
    t = d.add_table(rows=2, cols=2)
    t.rows[0].cells[0].text = "Owner"
    t.rows[0].cells[1].text = "Task"
    t.rows[1].cells[0].text = "Scott"
    t.rows[1].cells[1].text = "Review"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_docx_rebuild_preserves_content_and_produces_valid_docx():
    import io
    import docx
    from app.services.docx_rebuild import rebuild_docx
    rebuilt = rebuild_docx(_make_source_docx())
    d = docx.Document(io.BytesIO(rebuilt))
    texts = [p.text for p in d.paragraphs]
    assert "Onboarding Guide" in texts
    assert any("Welcome aboard" == t for t in texts)
    heading = next(p for p in d.paragraphs if p.text == "Onboarding Guide")
    assert heading.style.name == "Title"  # leading heading promoted to Title
    bullets = [p for p in d.paragraphs if p.text in ("First item", "Second item")]
    assert all(p.style.name == "List Bullet" for p in bullets)
    bold_run = next(r for p in d.paragraphs for r in p.runs if r.text == "aboard")
    assert bold_run.bold is True
    assert len(d.tables) == 1
    assert d.tables[0].rows[1].cells[0].text == "Scott"


def test_docx_rebuild_fails_open_on_garbage():
    from app.services.docx_rebuild import rebuild_docx
    garbage = b"not a docx at all"
    assert rebuild_docx(garbage) == garbage


def test_adapter_steers_docx_toolchain_on_generation():
    api_body, _ = _adapter()._build_body(_body(True))
    sys_texts = " ".join(b.get("text", "") for b in api_body["system"])
    assert "python-docx" in sys_texts and "docx.js" in sys_texts
    # non-generation turns get no steering
    api_body2, _ = _adapter()._build_body(_body(False))
    sys_texts2 = " ".join(b.get("text", "") for b in api_body2["system"])
    assert "python-docx" not in sys_texts2


def test_docx_rebuild_checklist_glyphs_drop_the_bullet():
    import io
    import docx
    from app.services.docx_rebuild import rebuild_docx
    d = docx.Document()
    d.add_paragraph("\u2610 Request ServiceNow access", style="List Bullet")
    d.add_paragraph("Regular bullet item", style="List Bullet")
    buf = io.BytesIO(); d.save(buf)
    out = docx.Document(io.BytesIO(rebuild_docx(buf.getvalue())))
    check = next(p for p in out.paragraphs if "ServiceNow" in p.text)
    plain = next(p for p in out.paragraphs if "Regular bullet" in p.text)
    assert check.style.name == "Normal"        # glyph is the marker
    assert plain.style.name == "List Bullet"   # real bullets keep the style


def test_docx_rebuild_title_header_and_page_footer():
    import io
    import docx
    from app.services.docx_rebuild import rebuild_docx
    d = docx.Document()
    d.add_heading("Quarterly Plan", level=1)
    d.add_heading("Goals", level=2)
    d.add_paragraph("Ship phase two.")
    buf = io.BytesIO(); d.save(buf)
    out = docx.Document(io.BytesIO(rebuild_docx(buf.getvalue())))
    assert out.paragraphs[0].style.name == "Title"
    assert out.paragraphs[0].text == "Quarterly Plan"
    goals = next(p for p in out.paragraphs if p.text == "Goals")
    assert goals.style.name == "Heading 2"          # section headings untouched
    sec = out.sections[0]
    assert sec.header.paragraphs[0].text == "Quarterly Plan"
    footer_xml = sec.footer.paragraphs[0]._p.xml
    assert 'w:instr=" PAGE "' in footer_xml and 'w:instr=" NUMPAGES "' in footer_xml


# --- conversational confirmation (Part 1 v2) ---

def test_offer_store_one_shot_and_ttl(monkeypatch):
    from app.services import generation_offers as go
    oid = go.create("u1", "docx", "for onboarding")
    assert go.take("u2", oid) is None            # not yours
    offer = go.take("u1", oid)
    assert offer == {"format": "docx", "gist": "for onboarding",
                     "expires": offer["expires"]}
    assert go.take("u1", oid) is None            # one-shot: dead after a reply
    oid2 = go.create("u1", "xlsx", "")
    monkeypatch.setattr(go, "OFFER_TTL_S", 0)
    key = ("u1", oid2)
    go._OFFERS[key]["expires"] -= 9999
    assert go.take("u1", oid2) is None           # expired


def test_offer_envelope_conversational_with_gist_and_offer_id():
    from app.services.document_generation import _CONFIRMATION_DEFAULTS, build_offer_envelope
    env = build_offer_envelope(_CONFIRMATION_DEFAULTS, "docx",
                               gist="for onboarding new people", offer_id="abc123")
    cta = env["feature_state"]["cta"]
    assert cta["text"] == ("Sounds like you want a Word document for "
                           "onboarding new people. Want me to build it?")
    assert cta["details"]["offer_id"] == "abc123"
    assert cta["details"]["gist"] == "for onboarding new people"
    # no gist -> falls back to the original template, no dangling braces
    env2 = build_offer_envelope(_CONFIRMATION_DEFAULTS, "xlsx", gist="", offer_id="x")
    assert "{" not in env2["feature_state"]["cta"]["text"]


@pytest.mark.asyncio
async def test_interpreter_confirms_declines_and_revises():
    from unittest.mock import AsyncMock, MagicMock
    from app.services.document_generation import interpret_offer_reply
    offer = {"format": "docx", "gist": "for onboarding"}

    router = MagicMock()
    router.route = AsyncMock(return_value=MagicMock(
        text='{"confirm": true, "format": null}'))
    out = await interpret_offer_reply(router, offer, "yes go ahead")
    assert out == {"confirm": True, "format": "docx"}      # offered format kept

    router.route = AsyncMock(return_value=MagicMock(
        text='{"confirm": true, "format": "xlsx"}'))
    out = await interpret_offer_reply(router, offer, "actually make it a spreadsheet")
    assert out == {"confirm": True, "format": "xlsx"}      # revised intent

    router.route = AsyncMock(return_value=MagicMock(
        text='{"confirm": false, "format": null}'))
    out = await interpret_offer_reply(router, offer, "no, what time is the standup?")
    assert out["confirm"] is False

    router.route = AsyncMock(side_effect=RuntimeError("boom"))
    out = await interpret_offer_reply(router, offer, "yes")
    assert out["confirm"] is False                          # fail-open = not armed


def test_chat_confirm_arms_generation_on_the_reply_turn(client, free_user, mock_provider,
                                                        tmp_db_path, monkeypatch):
    import sqlite3
    from unittest.mock import AsyncMock
    import app.services.document_generation as dg
    from app.services import generation_offers as go
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    uid = free_user.get("user_id")
    if uid is None:
        con = sqlite3.connect(tmp_db_path)
        uid = con.execute("SELECT id FROM users WHERE email LIKE 'test-free-user%'").fetchone()[0]
        con.close()
    oid = go.create(uid, "docx", "for onboarding")
    monkeypatch.setattr(dg, "interpret_offer_reply",
                        AsyncMock(return_value={"confirm": True, "format": "docx"}))

    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "generation_id": "gen-chat-yes"},
        user_content="yes, go ahead",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: generation_result" in r.text
    con = sqlite3.connect(tmp_db_path)
    row = con.execute("SELECT status FROM generations WHERE generation_id='gen-chat-yes'").fetchone()
    con.close()
    assert row and row[0] == "done"
    assert go.take(uid, oid) is None            # offer consumed


def test_chat_decline_is_a_normal_turn(client, free_user, mock_provider,
                                       tmp_db_path, monkeypatch):
    import sqlite3
    from unittest.mock import AsyncMock
    import app.services.document_generation as dg
    from app.services import generation_offers as go
    from tests.conftest import chat_request

    _enable_confirmed_generation(client)
    uid = free_user.get("user_id")
    if uid is None:
        con = sqlite3.connect(tmp_db_path)
        uid = con.execute("SELECT id FROM users WHERE email LIKE 'test-free-user%'").fetchone()[0]
        con.close()
    oid = go.create(uid, "docx", "for onboarding")
    monkeypatch.setattr(dg, "interpret_offer_reply",
                        AsyncMock(return_value={"confirm": False, "format": "docx"}))

    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "generation_id": "gen-chat-no"},
        user_content="no thanks, what time is the standup?",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "feature_state" not in r.json()      # mock classifier junk fails open
    con = sqlite3.connect(tmp_db_path)
    row = con.execute("SELECT 1 FROM generations WHERE generation_id='gen-chat-no'").fetchone()
    con.close()
    assert row is None                           # ids discarded on decline
