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


# --- transcript language picker, renditions-driven (Scott 2026-08-24 rulings) --
# Picker = Original + the languages the sender already translated in the
# app (bundle transcriptRenditions), opens on manifest share_language,
# no web-side translation, no picker when nothing was translated, and
# the translation REPLACES the original in the same window.

SEGS = [{"text": "Hola a todos.", "speakerLabel": "A", "sessionTimeOffset": 0.0, "endTimeOffset": 2.5},
        {"text": "Empecemos.", "speakerLabel": "B", "sessionTimeOffset": 2.5, "endTimeOffset": 4.0}]
# The measured shape (Scott's share ddfeb96f): a transcript TURN spans
# several segments, so the transcript text has fewer lines than segments.
SEGS3 = [{"text": "Hola a todos.", "speakerLabel": "A", "sessionTimeOffset": 0.0, "endTimeOffset": 2.5},
         {"text": "Bienvenidos.", "speakerLabel": "A", "sessionTimeOffset": 2.5, "endTimeOffset": 3.5},
         {"text": "Empecemos.", "speakerLabel": "B", "sessionTimeOffset": 3.5, "endTimeOffset": 5.0}]


def _bundle_rend(renditions=None, share_language=None, spoken="es", segments=True, images=0,
                 segs=None, transcript=None):
    segs = SEGS if segs is None else segs
    rec = {"title": "Kickoff", "durationSeconds": 90.0, "rollingSummary": "Resumen corto.",
           "transcript": transcript if transcript is not None else "\n".join(f"[{x['speakerLabel']}] {x['text']}" for x in segs),
           "transcriptLanguage": spoken}
    if segments:
        rec["transcriptSegments"] = segs
    if renditions is not None:
        rec["transcriptRenditions"] = renditions
    manifest = {"formatVersion": 1}
    if share_language:
        manifest["share_language"] = share_language
    entries = {"manifest.json": json.dumps(manifest).encode(),
               f"meetings/{ORIGIN}.json": json.dumps(rec, ensure_ascii=False).encode()}
    for i in range(images):
        entries[f"media/{ORIGIN}/images/img-{i}.jpg"] = b"\xff\xd8\xff" + bytes([i]) * 50
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


EN = {"lang": "en", "engine_version": 1, "created_at": "2026-08-24T18:00:00Z",
      "transcript": "[A] Hello everyone.\n[B] Let's begin.", "summary": "Short summary.", "report_html": None}


def test_picker_offers_only_original_plus_rendition_langs_and_opens_on_share_language(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="en"))
    page = client.get(f"/s/{token}").text
    assert "<select class='lang'>" in page
    assert "<option value='en' selected>English</option>" in page
    assert "value='fr'" not in page and "value='ja'" not in page and "value='es'" not in page
    # the translated VIEW lives inside the same window as the original, with the segment timing
    seg_window = page[page.index("<div class='segs'>"):page.index("</details>", page.index("<div class='segs'>"))]
    assert "<div class='view' data-group='tx' data-lang=''>" in seg_window and "<div class='view' data-group='tx' data-lang='en'" in seg_window
    assert "data-s='2.50' data-e='4.00'><b>B</b> Let&#39;s begin.</p>" in seg_window
    # summary is rendered markdown now (a paragraph), stored as safe HTML for the swap
    assert "data-sum-en='&lt;p&gt;Short summary.&lt;/p&gt;'" in page
    assert "data-sum-orig='&lt;p&gt;Resumen corto.&lt;/p&gt;'" in page
    assert "<div class='summary' data-sum-orig=" in page
    assert "/transcript?lang=" not in page


def test_turn_lines_that_span_segments_align_by_grouping(client, pro_user):
    rend = dict(EN, transcript="[A] Hello everyone. Welcome.\n[B] Let's begin.")   # 2 lines over 3 segments
    token = _share(client, pro_user, _bundle_rend([rend], share_language="en", segs=SEGS3,
                                                  transcript="[A] Hola a todos. Bienvenidos.\n[B] Empecemos."))
    page = client.get(f"/s/{token}").text
    en_view = page[page.index("<div class='view' data-group='tx' data-lang='en'"):page.index("</div>", page.index("<div class='view' data-group='tx' data-lang='en'"))]
    assert "data-s='0.00' data-e='3.50'><b>A</b> Hello everyone. Welcome.</p>" in en_view   # spans segs 0-1
    assert "data-s='3.50' data-e='5.00'><b>B</b> Let&#39;s begin.</p>" in en_view
    assert "<pre class='rend'>" not in page


def test_no_picker_when_nothing_was_translated(client, pro_user):
    token = _share(client, pro_user, _bundle_rend(None))
    page = client.get(f"/s/{token}").text
    assert "<select class='lang'>" not in page and "data-lang='en'" not in page


def test_misaligned_rendition_shows_in_place_as_plain_text_not_below(client, pro_user):
    bad = dict(EN, transcript="Hello everyone. Let's begin. Extra line.\nMore.\nMore.")   # 3 lines, 2 segs, 2 turns
    token = _share(client, pro_user, _bundle_rend([bad], share_language="en"))
    page = client.get(f"/s/{token}").text
    window = page[page.index("<div class='segs'>"):page.index("</details>", page.index("<div class='segs'>"))]
    assert "<div class='view' data-group='tx' data-lang='en' style='display:none'><pre class='rend'>" in window   # inside the window
    assert page.count("<pre class='rend'>") == 1
    assert "<option value='en' selected>English</option>" in page


def test_transcript_window_is_scrollable_and_route_is_gone(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="en"))
    page = client.get(f"/s/{token}").text
    assert "max-height:60vh;overflow-y:auto" in page
    assert "box.scrollTop=" in page
    assert client.get(f"/s/{token}/transcript?lang=en").status_code == 404


def test_contents_descriptor_comes_off_the_bytes_localized_to_share_language(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="en", images=3))
    page = client.get(f"/s/{token}").text
    assert "Transcript · 3 photos" in page
    assert "content='Transcript · 3 photos" in page
    token2 = _share(client, pro_user, _bundle_rend(None, share_language="es", images=1))
    assert "Transcripción · 1 foto" in client.get(f"/s/{token2}").text


# --- photos shared with the meeting (Scott 2026-08-24) ------------------------

def test_photos_render_as_a_gallery_and_serve_from_the_bundle(client, pro_user):
    token = _share(client, pro_user, _bundle_rend(None, images=3))
    page = client.get(f"/s/{token}").text
    assert "<div class='gallery'>" in page
    for n in range(3):
        assert f"data-full='https://share.shouldersurf.com/s/{token}/image/{n}'" in page
    # a lightbox overlay opens on top (never replaces the page)
    assert "<div class='lb'" in page and "class='thumb'" in page and "target='_blank'" not in page
    assert "e.key==='Escape'" in page and "ArrowLeft" in page and "ArrowRight" in page
    r = client.get(f"/s/{token}/image/1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8\xff" + bytes([1]) * 50
    assert client.get(f"/s/{token}/image/3").status_code == 404
    assert client.head(f"/s/{token}/image/0").status_code == 200
    assert "<div class='gallery'>" not in client.get(f"/s/{_share(client, pro_user, _bundle_rend(None))}").text


# --- App Store badge + QR on every hosted meeting (Scott 2026-08-24) ----------

def test_page_carries_the_badge_and_qr_localized_to_the_shared_language(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="es"))
    page = client.get(f"/s/{token}").text
    assert "https://apps.apple.com/app/id6760098225" in page
    assert "badges/download-on-the-app-store/black/es-es" in page
    assert "src='https://shouldersurf.com/appstore-qr.svg?v=2'" in page
    assert "Apunta la cámara de tu iPhone" in page
    # an English meeting with nothing translated: English block (the
    # block follows the language the page shows, Scott 2026-08-25)
    token2 = _share(client, pro_user, _bundle_rend(None, spoken="en"))
    page2 = client.get(f"/s/{token2}").text
    assert "black/en-us" in page2 and "Point your iPhone camera here to download." in page2
    # at the very bottom now (Scott 2026-08-24): after the meeting content
    assert page2.index("<div class='dl'>") > page2.rindex("</section>")


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


def test_photos_open_in_a_lightbox_and_download_block_is_at_the_bottom(client, pro_user):
    token = _share(client, pro_user, _bundle_rend(None, images=2))
    page = client.get(f"/s/{token}").text
    # thumbnails are buttons that feed the overlay, not links that navigate away
    assert page.count("class='thumb'") == 2 and "target='_blank'" not in page
    assert "<div class='lb'" in page and ".lb.on{display:flex}" in page
    assert "class='prev'" in page and "class='next'" in page and "class='close'" in page
    # download block is the last content block, below the meeting
    assert page.index("<div class='dl'>") > page.rindex("</section>")
    # a share with no photos has no lightbox
    plain = client.get(f"/s/{_share(client, pro_user, _bundle_rend(None))}").text
    assert "<div class='lb'" not in plain or "class='thumb'" not in plain


def test_h1_prefers_the_display_title_from_the_header_over_a_raw_rec_title(client, pro_user):
    """On a translated share the client sends the localized displayTitle as
    X-Share-Title while the bundle's rec.title is the raw English one; the
    H1 must show the localized display title, matching the card and page."""
    import io as _io, json as _json, zipfile as _zip
    O = "0F1F2F3F-1111-4222-8333-999999999999"
    rec = {"title": "Project Scope Alignment English", "durationSeconds": 60.0,
           "rollingSummary": "s", "transcript": "hi", "transcriptLanguage": "en"}
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", _json.dumps({"formatVersion": 1, "share_language": "es"}))
        z.writestr(f"meetings/{O}.json", _json.dumps(rec, ensure_ascii=False))
    r = client.post("/v1/shares", content=buf.getvalue(), headers={**pro_user["headers"],
        "Content-Type": "application/vnd.shouldersurf.archive",
        "X-Share-Title": "Alineacion del Alcance del Proyecto",
        "X-Share-Date": "2026-08-24", "X-Share-Duration-Seconds": "60",
        "X-Share-Summary-Line": "s", "X-Share-Transcript-Included": "true"})
    assert r.status_code == 200, r.text
    page = client.get(f"/s/{r.json()['url'].rsplit('/',1)[1]}").text
    assert "<h1>Alineacion del Alcance del Proyecto</h1>" in page
    assert "Project Scope Alignment English" not in page


# --- global meeting-language control (Scott 2026-08-24: one surface, at the top) ---

ESREND = {"lang": "es", "engine_version": 1, "created_at": "2026-08-24T18:00:00Z",
          "transcript": "[A] Hola a todos.\n[B] Empecemos.", "summary": "## Resumen\n- Casos", "report_html": None}


def test_language_bar_is_at_the_top_one_control_localized_to_the_shared_language(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([ESREND], share_language="es", spoken="en"))
    page = client.get(f"/s/{token}").text
    # exactly one language control, in the top bar, above the CTA and the meeting
    assert page.count("<select class='lang'>") == 1
    assert "<div class='langbar'>" in page
    assert page.index("langbar") < page.index("class='get'") < page.index("<section>")
    # opens on the shared language (Spanish), offers Original + Spanish only
    assert "value='es' selected" in page
    assert "value='fr'" not in page
    # the label and CTA render in the shared language by default, with swap attrs
    assert "Ver la reunión en" in page
    assert "Abrir en Shoulder Surf" in page and "data-i18n-orig='Open in Shoulder Surf'" in page
    assert "Mostrar transcripción" in page and "data-i18n-orig='Show transcript'" in page
    # the transcript picker is no longer inside the transcript details
    details = page[page.index("<details class='tx'"):page.index("</details>", page.index("<details class='tx'"))]
    assert "<select" not in details


def test_toggle_drives_the_whole_page_globally(client, pro_user):
    token = _share(client, pro_user, _bundle_rend([EN], share_language="es"))
    page = client.get(f"/s/{token}").text
    # one applier list fed by the single top select; summary and views both swap
    assert "select.lang" in page and "applyLang" in page and "apply.push(setLang)" in page
    assert "data-sum-en=" in page and "<div class='view' data-group='tx' data-lang='en'" in page
    # chrome swaps by data-i18n-<lang>
    assert "data-i18n-es='Abrir en Shoulder Surf'" in page and "el.getAttribute('data-i18n-'+lang)" in page


def test_no_translations_means_no_language_bar(client, pro_user):
    token = _share(client, pro_user, _bundle_rend(None, share_language=None))
    page = client.get(f"/s/{token}").text
    assert "<div class='langbar'>" not in page and "<select class='lang'>" not in page
    # chrome follows the language the page shows (Scott 2026-08-25): a
    # Spanish original with nothing translated gets Spanish chrome, with
    # the English string available only as a swap value
    assert ">Abrir en Shoulder Surf</span>" in page and "data-i18n-en='Open in Shoulder Surf'" in page
    assert "<html lang='es' data-orig-lang='es'>" in page
    # an English original with nothing translated still reads English
    page_en = client.get(f"/s/{_share(client, pro_user, _bundle_rend(None, share_language=None, spoken='en'))}").text
    assert ">Open in Shoulder Surf</span>" in page_en and "<html lang='en' data-orig-lang='en'>" in page_en


# --- report is part of the language surface + shareLanguage default (Scott 2026-08-24) ---

def _bundle_report(share_language_key="shareLanguage", share_language="es"):
    import io as _io, json as _json, zipfile as _zip
    O = "0B0B0B0B-1111-4222-8333-777777777777"
    rec = {"title": "Meta Legal", "durationSeconds": 261.0,
           "reportHTML": "<html><body><h1>Report English</h1></body></html>",
           "transcript": "[A] Hello.", "transcriptLanguage": "en",
           "transcriptRenditions": [{"lang": "es", "transcript": "[A] Hola.", "summary": "Resumen",
                                     "report_html": "<html><body><h1>Informe Espanol</h1></body></html>"}],
           "transcriptSegments": [{"text": "Hello.", "speakerLabel": "A", "sessionTimeOffset": 0.0, "endTimeOffset": 2.0}]}
    man = {"formatVersion": 1}
    if share_language:
        man[share_language_key] = share_language
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", _json.dumps(man))
        z.writestr(f"meetings/{O}.json", _json.dumps(rec, ensure_ascii=False))
    return buf.getvalue()


def test_camelcase_shareLanguage_makes_the_page_default_to_the_shared_language(client, pro_user):
    # SS writes shareLanguage (camelCase); reading share_language (snake) made
    # every Spanish share open in English.
    token = _share(client, pro_user, _bundle_report("shareLanguage", "es"))
    page = client.get(f"/s/{token}").text
    assert "value='es' selected" in page                 # opens in Spanish
    assert "Ver la reunión en" in page                   # chrome in Spanish
    # the snake_case form still works as a fallback
    token2 = _share(client, pro_user, _bundle_report("share_language", "es"))
    assert "value='es' selected" in client.get(f"/s/{token2}").text


def test_the_report_toggles_language_with_the_summary_and_transcript(client, pro_user):
    token = _share(client, pro_user, _bundle_report())
    page = client.get(f"/s/{token}").text
    # both report views present, grouped, toggled by the one control
    assert "data-group='report' data-lang=''" in page and "data-group='report' data-lang='es'" in page
    assert "Report English" in page and "Informe Espanol" in page
    # transcript views are grouped separately so audio sync still finds segments
    assert "data-group='tx'" in page and "tx=shown" in page
    # the report has no per-report picker; the top control drives it
    assert page.count("<select class='lang'>") == 1


# --- Original toggle after a rendition (Scott 2026-08-25, via CQ) --------------
# Real topology: meeting SPOKEN in Spanish, translated to French in the
# app, shared in French. SS's X-Share-Title is the French display title,
# rec.title the Spanish original. Switching back to Original showed a
# French h1, English chrome and French date labels on a Spanish page.

FRREND = {"lang": "fr", "engine_version": 1, "created_at": "2026-08-25T18:45:30Z",
          "transcript": "[A] Bonjour à tous.\n[B] Commençons.",
          "summary": "# Résumé de Réunion\n- Origine",
          "report_html": "<html><body><h1>Rapport</h1></body></html>"}


def _bundle_scott(share_language="fr", renditions=(FRREND,), spoken="es"):
    rec = {"title": "Origen del negocio", "durationSeconds": 420.5, "date": 809036193.487709,
           "reportHTML": "<html><body><h1>Report English</h1></body></html>",
           "transcript": "[A] Hola a todos.\n[B] Empecemos.", "transcriptLanguage": spoken,
           "transcriptSegments": SEGS, "transcriptRenditions": list(renditions)}
    man = {"formatVersion": 1}
    if share_language:
        man["shareLanguage"] = share_language
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(man))
        z.writestr(f"meetings/{ORIGIN}.json", json.dumps(rec, ensure_ascii=False))
    return buf.getvalue()


def _share_titled(client, user, body, title):
    from urllib.parse import quote
    # the X-Share-* contract: UTF-8, percent-encoded (see routers/shares._card_text)
    r = client.post("/v1/shares", content=body, headers={**user["headers"], **HDRS, "X-Share-Title": quote(title, safe="")})
    assert r.status_code == 200, r.text
    return r.json()["url"].rsplit("/", 1)[1]


def test_original_is_labelled_with_its_autonym_and_every_label_swaps_back_to_it(client, pro_user):
    token = _share_titled(client, pro_user, _bundle_scott(), "Origine de la société")
    page = client.get(f"/s/{token}").text
    # opens in French (the shared language has a rendition)
    assert "<html lang='fr' data-orig-lang='es'>" in page
    assert "value='fr' selected" in page
    # the Original option names the language it switches into
    assert "<option value=''>Original (Español)</option>" in page
    # the h1 shows the French display title and carries the Spanish original for the swap back
    assert ("<h1 data-i18n-orig='Origen del negocio' data-i18n-es='Origen del negocio' "
            "data-i18n-fr='Origine de la société'>Origine de la société</h1>") in page
    # chrome renders French and swaps to SPANISH under Original (was English)
    assert ">Voir la réunion en</span>" in page and "data-i18n-orig='Ver la reunión en'" in page
    assert ">Ouvrir dans Shoulder Surf</span>" in page and "data-i18n-orig='Abrir en Shoulder Surf'" in page
    assert ">Afficher la transcription</summary>" in page and "data-i18n-orig='Mostrar transcripción'" in page
    assert "data-i18n-en='Open in Shoulder Surf'" in page
    # the date line (date, contents) is part of the surface too
    meta = page[page.index("<p class='dim' data-i18n-orig="):page.index("</p>", page.index("<p class='dim' data-i18n-orig="))]
    assert "data-i18n-orig='21 de agosto de 2026, 20:16 UTC" in meta and "Informe · Transcripción" in meta
    assert meta.endswith("Rapport · Transcription") and "21 août 2026, 20:16 UTC" in meta.rsplit(">", 1)[1]
    # the date line carries a value per language the CONTROL offers
    # (Original + the renditions), not one per locale we know: nothing on
    # this page can select English, so there is no English value to swap in
    assert "data-i18n-en=" not in meta
    # footer likewise
    assert ">Partagé depuis Shoulder Surf. Ce lien cesse de fonctionner le " in page
    assert "data-i18n-orig='Compartido desde Shoulder Surf." in page
    # the report keeps both views; the JS keeps <html lang> honest on toggle
    assert "data-group='report' data-lang=''" in page and "data-group='report' data-lang='fr'" in page
    assert "document.documentElement.lang=lang||document.documentElement.getAttribute('data-orig-lang')" in page


def test_a_share_that_opens_on_original_keeps_the_display_title_and_original_chrome(client, pro_user):
    # shared in Spanish (the original) while a French rendition exists
    token = _share_titled(client, pro_user, _bundle_scott(share_language="es"), "Origen del negocio (display)")
    page = client.get(f"/s/{token}").text
    assert "<html lang='es' data-orig-lang='es'>" in page
    assert "<option value='' selected>Original (Español)</option>" in page and "value='fr'>Français" in page
    # no rendition matches the shared language, so the h1 is the display title, no swap needed
    assert "<h1>Origen del negocio (display)</h1>" in page
    assert ">Ver la reunión en</span>" in page and ">Abrir en Shoulder Surf</span>" in page
    assert "21 de agosto de 2026, 20:16 UTC" in page


def test_original_language_and_date_line_helpers():
    from datetime import datetime, timezone
    from app.services.share_bundle import _when_str, original_language, page_languages
    d = datetime(2026, 8, 21, 20, 16, tzinfo=timezone.utc)
    assert _when_str(d, "en") == "August 21, 2026, 8:16 PM UTC"
    assert _when_str(d, "es-US") == "21 de agosto de 2026, 20:16 UTC"
    assert _when_str(d, "fr") == "21 août 2026, 20:16 UTC"
    assert _when_str(d, "ja") == "2026年8月21日 20:16 UTC"
    assert _when_str(d, "de") == "August 21, 2026, 8:16 PM UTC"
    assert _when_str(None, "es") == ""
    assert original_language([{"record": {"transcriptLanguage": "es-MX"}}]) == "es"
    assert original_language([{"record": {"transcriptLanguage": "es"}}, {"record": {"transcriptLanguage": "en"}}]) == "en"
    assert original_language([{"record": {}}]) == "en"
    rec = {"transcript": "[A] Hola a todos.\n[B] Empecemos.", "transcriptSegments": SEGS,
           "transcriptRenditions": [FRREND, {"lang": "de", "transcript": "   "}]}
    assert page_languages([{"record": rec}], True) == ["fr"]
    assert page_languages([{"record": rec}], False) == []
