"""Share page audio (Scott 2026-08-24): the recording plays on the hosted
page and the transcript highlights the line being spoken. Route, sidecar,
Range, renderer, and purge, all against a real zip bundle."""
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app.services.share_bundle import (
    MAX_AUDIO_ENTRY_BYTES, extract_audio_sidecar, list_audio_entries, render_share_page,
)

ORIGIN = "2C1D0A7E-6F2E-4F2C-9B7E-0F1D2C3B4A59"
AUDIO = b"\x00\x00\x00\x18ftypM4A " + bytes(range(256)) * 40  # 10 KB of "m4a"
HDRS = {"Content-Type": "application/vnd.shouldersurf.archive",
        "X-Share-Title": "Audio kickoff", "X-Share-Date": "2026-08-24",
        "X-Share-Duration-Seconds": "90", "X-Share-Summary-Line": "Talked.",
        "X-Share-Transcript-Included": "true"}


def _bundle(with_audio=True, with_segments=True, n_audio=1):
    rec = {"title": "Audio kickoff", "durationSeconds": 90.0, "rollingSummary": "Talked.",
           "transcript": "Hola a todos. Empecemos."}
    if with_segments:
        rec["transcriptSegments"] = [
            {"text": "Hola a todos.", "speakerLabel": "Speaker 1", "sessionTimeOffset": 0.0, "endTimeOffset": 2.5},
            {"text": "Empecemos.", "speakerLabel": "Speaker 2", "sessionTimeOffset": 2.5, "endTimeOffset": 4.0},
        ]
    entries = {"manifest.json": json.dumps({"formatVersion": 1}).encode(),
               f"meetings/{ORIGIN}.json": json.dumps(rec).encode()}
    if with_audio:
        for i in range(n_audio):
            entries[f"media/{ORIGIN}/audio/session-{i}.m4a"] = AUDIO
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def _share(client, user, body):
    r = client.post("/v1/shares", content=body, headers={**user["headers"], **HDRS})
    assert r.status_code == 200, r.text
    return r.json()["url"].rsplit("/", 1)[1]


# --- bundle helpers ----------------------------------------------------------

def test_list_audio_entries_finds_only_audio_under_media(tmp_path):
    p = tmp_path / "b.zip"; p.write_bytes(_bundle(n_audio=2))
    assert list_audio_entries(str(p)) == {ORIGIN: [f"media/{ORIGIN}/audio/session-0.m4a",
                                                    f"media/{ORIGIN}/audio/session-1.m4a"]}
    q = tmp_path / "n.zip"; q.write_bytes(_bundle(with_audio=False))
    assert list_audio_entries(str(q)) == {}
    assert list_audio_entries(b"not a zip") == {}


def test_extract_sidecar_writes_bytes_and_refuses_oversized(tmp_path, monkeypatch):
    p = tmp_path / "b.zip"; p.write_bytes(_bundle())
    side = str(tmp_path / "b.zip.audio0.m4a")
    assert extract_audio_sidecar(str(p), f"media/{ORIGIN}/audio/session-0.m4a", side)
    assert Path(side).read_bytes() == AUDIO
    assert not extract_audio_sidecar(str(p), "media/nope.m4a", str(tmp_path / "x"))
    monkeypatch.setattr("app.services.share_bundle.MAX_AUDIO_ENTRY_BYTES", 100)
    side2 = str(tmp_path / "b.zip.audio1.m4a")
    assert not extract_audio_sidecar(str(p), f"media/{ORIGIN}/audio/session-0.m4a", side2)
    assert not Path(side2).exists() and not Path(side2 + ".part").exists()


# --- renderer ----------------------------------------------------------------

def _rendered(bundle_bytes, audio):
    from app.services.share_bundle import read_bundle
    return render_share_page(read_bundle(bundle_bytes), card_title="t", card_desc="d",
                             transcript_included=True, expires_at="2026-09-01T00:00:00+00:00",
                             share_url="https://share.example.com/s/TOKEN", audio_by_origin=audio)


def test_page_carries_player_and_timed_segments_when_bundle_has_audio():
    html = _rendered(_bundle(), {ORIGIN: [f"media/{ORIGIN}/audio/session-0.m4a"]})
    assert "<audio class='sa' controls" in html
    assert "src='https://share.example.com/s/TOKEN/audio/0'" in html
    assert "data-s='0.00' data-e='2.50'" in html and "data-s='2.50' data-e='4.00'" in html
    assert "<details class='tx' open data-origin=" in html   # opens itself when there is audio
    assert "timeupdate" in html and "currentTime" in html  # the sync script rides


def test_page_without_audio_has_no_player_but_keeps_segments_closed():
    html = _rendered(_bundle(with_audio=False), {})
    assert "<audio" not in html
    assert "data-s='0.00'" in html
    assert "<details class='tx' data-origin=" in html


def test_page_without_segments_falls_back_to_plain_transcript():
    html = _rendered(_bundle(with_segments=False), {ORIGIN: [f"media/{ORIGIN}/audio/session-0.m4a"]})
    assert "<audio" in html and "<pre class='orig-plain'>" in html and "data-s=" not in html


# --- route -------------------------------------------------------------------

def test_audio_route_serves_bytes_with_range_support(client, pro_user):
    token = _share(client, pro_user, _bundle())
    r = client.get(f"/s/{token}/audio/0")
    assert r.status_code == 200 and r.content == AUDIO
    assert r.headers["content-type"].startswith("audio/mp4")
    assert r.headers.get("accept-ranges") == "bytes"
    part = client.get(f"/s/{token}/audio/0", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == AUDIO[100:200]
    assert part.headers.get("content-range") == f"bytes 100-199/{len(AUDIO)}"
    # Safari's audio element probes with bytes=0-1 and can refuse to play
    # without a real 206 (Bifrost, 2026-08-24, measured on the card PNG).
    probe = client.get(f"/s/{token}/audio/0", headers={"Range": "bytes=0-1"})
    assert probe.status_code == 206 and probe.content == AUDIO[:2]
    assert probe.headers.get("content-range") == f"bytes 0-1/{len(AUDIO)}"
    head = client.head(f"/s/{token}/audio/0")
    assert head.status_code == 200 and head.content == b""


def test_audio_route_404s_past_the_entries_and_on_bundles_without_audio(client, pro_user):
    token = _share(client, pro_user, _bundle())
    assert client.get(f"/s/{token}/audio/1").status_code == 404
    token2 = _share(client, pro_user, _bundle(with_audio=False))
    assert client.get(f"/s/{token2}/audio/0").status_code == 404
    assert client.get("/s/notatoken/audio/0").status_code == 410


def test_page_route_wires_the_player_to_this_share(client, pro_user):
    token = _share(client, pro_user, _bundle())
    page = client.get(f"/s/{token}")
    assert page.status_code == 200
    assert f"/s/{token}/audio/0" in page.text and "data-s='2.50'" in page.text


@pytest.mark.anyio
async def test_purge_deletes_the_sidecar_with_the_share(client, pro_user, tmp_db_path):
    token = _share(client, pro_user, _bundle())
    assert client.get(f"/s/{token}/audio/0").status_code == 200
    conn = sqlite3.connect(tmp_db_path)
    storage_path = conn.execute("SELECT storage_path FROM meeting_shares WHERE token=?", (token,)).fetchone()[0]
    sidecar = Path(storage_path + ".audio0.m4a")
    assert sidecar.exists()
    conn.execute("UPDATE meeting_shares SET expires_at='2000-01-01T00:00:00+00:00' WHERE token=?", (token,))
    conn.commit(); conn.close()
    import aiosqlite
    from app.services.meeting_shares import purge_expired
    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        assert await purge_expired(db) == 1
    assert not sidecar.exists() and not Path(storage_path).exists()


# --- multi-meeting bundles: one player per ORIGIN, n shared by route and page

ORIGIN2 = "9E9E9E9E-2222-4333-8444-555555555555"
AUDIO2 = b"\x00\x00\x00\x18ftypM4A second" + bytes(range(255, -1, -1)) * 40


def _two_meeting_bundle():
    def rec(title, text):
        return json.dumps({"title": title, "durationSeconds": 30.0, "rollingSummary": "s", "transcript": text,
                           "transcriptSegments": [{"text": text, "speakerLabel": "A",
                                                   "sessionTimeOffset": 0.0, "endTimeOffset": 3.0}]}).encode()
    # Origins deliberately inserted out of sorted order: the ordering rule,
    # not zip order, decides n.
    entries = {"manifest.json": json.dumps({"formatVersion": 1}).encode(),
               f"meetings/{ORIGIN2}.json": rec("Second", "segunda"),
               f"media/{ORIGIN2}/audio/a.m4a": AUDIO2,
               f"meetings/{ORIGIN}.json": rec("First", "primera"),
               f"media/{ORIGIN}/audio/a.m4a": AUDIO}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_multi_meeting_bundle_numbers_players_the_way_the_route_does(client, pro_user):
    token = _share(client, pro_user, _two_meeting_bundle())
    page = client.get(f"/s/{token}").text
    # ORIGIN sorts before ORIGIN2 ("2C..." < "9E..."), so First is /audio/0 and Second /audio/1
    first = page.index("<h1>First</h1>"); second = page.index("<h1>Second</h1>")
    assert f"/s/{token}/audio/0'" in page[first:second]
    assert f"/s/{token}/audio/1'" in page[second:]
    assert client.get(f"/s/{token}/audio/0").content == AUDIO
    assert client.get(f"/s/{token}/audio/1").content == AUDIO2
    assert client.get(f"/s/{token}/audio/2").status_code == 404
    # sync is scoped per section, never bound to the first player globally
    assert "querySelectorAll('section')" in page and "A[0]" not in page


# --- transcript language picker, renditions-driven (Scott 2026-08-24 ruling) ---
# Picker = Original + the languages the sender already translated in the
# app (bundle transcriptRenditions), opens on manifest share_language,
# no web-side translation, no picker when nothing was translated.

SEGS = [{"text": "Hola a todos.", "speakerLabel": "A", "sessionTimeOffset": 0.0, "endTimeOffset": 2.5},
        {"text": "Empecemos.", "speakerLabel": "B", "sessionTimeOffset": 2.5, "endTimeOffset": 4.0}]


def _bundle_rend(renditions=None, share_language=None, spoken="es", segments=True, images=0):
    rec = {"title": "Kickoff", "durationSeconds": 90.0, "rollingSummary": "Resumen corto.",
           "transcript": "A Hola a todos.\nB Empecemos.", "transcriptLanguage": spoken}
    if segments:
        rec["transcriptSegments"] = SEGS
    if renditions is not None:
        rec["transcriptRenditions"] = renditions
    manifest = {"formatVersion": 1}
    if share_language:
        manifest["share_language"] = share_language
    entries = {"manifest.json": json.dumps(manifest).encode(),
               f"meetings/{ORIGIN}.json": json.dumps(rec, ensure_ascii=False).encode()}
    for i in range(images):
        entries[f"media/{ORIGIN}/images/img-{i}.jpg"] = b"\xff\xd8\xff" + bytes(50)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


EN = {"lang": "en", "engine_version": 1, "created_at": "2026-08-24T18:00:00Z",
      "transcript": "A Hello everyone.\nB Let's begin.", "summary": "Short summary.", "report_html": None}


def test_picker_offers_only_original_plus_rendition_langs_and_opens_on_share_language(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="en"))
    page = client.get(f"/s/{token}").text
    assert "<select class='lang'>" in page
    assert "<option value='en' selected>English</option>" in page
    assert "value='fr'" not in page and "value='ja'" not in page and "value='es'" not in page
    # aligned lines ride each segment with the same timing
    assert "data-tr-en='A Hello everyone.'" in page and "data-tr-en='B Let&#x27;s begin.'" in page or "data-tr-en='B Let" in page
    assert "data-s='2.50' data-e='4.00'" in page
    # translated summary rides the summary element
    assert "data-sum-en='Short summary.'" in page and "data-sum-orig='Resumen corto.'" in page
    # nothing on the page fetches a translation
    assert "/transcript?lang=" not in page


def test_no_picker_when_nothing_was_translated(client, pro_user):
    token = _share(client, pro_user, _bundle_rend(None))
    page = client.get(f"/s/{token}").text
    assert "<select class='lang'>" not in page and "data-tr-en=" not in page


def test_misaligned_rendition_falls_back_to_plain_text_not_a_guess(client, pro_user):
    bad = dict(EN, transcript="Hello everyone. Let's begin.")   # one line for two segments
    token = _share(client, pro_user, _bundle_rend([bad], share_language="en"))
    page = client.get(f"/s/{token}").text
    assert "data-tr-en=" not in page                                # never guessed onto lines
    assert "<pre class='rend' data-lang='en'" in page               # shown as plain text instead
    assert "<option value='en' selected>English</option>" in page   # still pickable


def test_transcript_window_is_scrollable_and_route_is_gone(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="en"))
    page = client.get(f"/s/{token}").text
    assert "max-height:60vh;overflow-y:auto" in page
    assert "box.scrollTop=" in page                                  # autoscroll inside the window
    assert client.get(f"/s/{token}/transcript?lang=en").status_code == 404


def test_contents_descriptor_comes_off_the_bytes_localized_to_share_language(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="en", images=3))
    page = client.get(f"/s/{token}").text
    assert "Transcript · 3 photos" in page                            # meta line (no report, no audio in this bundle)
    assert "content='Transcript · 3 photos" in page                   # og:description prefix
    token2 = _share(client, pro_user, _bundle_rend(None, share_language="es", images=1))
    assert "Transcripción · 1 foto" in client.get(f"/s/{token2}").text


# --- App Store badge + QR on every hosted meeting (Scott 2026-08-24) ----------

def test_page_carries_the_badge_and_qr_localized_to_the_shared_language(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="es"))
    page = client.get(f"/s/{token}").text
    assert "https://apps.apple.com/app/id6760098225" in page
    assert "badges/download-on-the-app-store/black/es-es" in page
    assert "src='https://shouldersurf.com/appstore-qr.svg?v=2'" in page
    assert "Apunta la cámara de tu iPhone" in page
    token2 = _share(client, pro_user, _bundle_rend(None))
    page2 = client.get(f"/s/{token2}").text
    assert "black/en-us" in page2 and "Point your iPhone camera here to download." in page2


def test_no_app_store_id_means_no_download_block(client, pro_user):
    from app.main import app
    share = app.state.remote_configs.setdefault("client-config", {}).setdefault("share", {})
    prev = share.get("app_store_id")
    share["app_store_id"] = ""
    try:
        token = _share(client, pro_user, _bundle_rend(None))
        page = client.get(f"/s/{token}").text
        assert "class='dl'" not in page and "appstore-qr.svg" not in page
    finally:
        if prev is None:
            share.pop("app_store_id", None)
        else:
            share["app_store_id"] = prev
