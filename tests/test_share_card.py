"""Ledger link-preview card (variant 2b), rendered server-side per share."""
import io
import json
import sqlite3
import zipfile
from pathlib import Path

from PIL import Image

from app.services.share_card import render_card

ORIGIN = "0C0C0C0C-1111-4222-8333-444444444444"
HDRS = {"Content-Type": "application/vnd.shouldersurf.archive", "X-Share-Title": "Q3 pricing kickoff",
        "X-Share-Date": "2026-08-24", "X-Share-Duration-Seconds": "2820",
        "X-Share-Summary-Line": "Converged on RAG over fine-tuning.", "X-Share-Transcript-Included": "true"}


def _bundle(share_language=None):
    import base64
    report = {"header": {"title": "Q3 pricing kickoff", "attendees": ["A", "B", "C"], "summary": "s"},
              "actions": [{"task": "x", "priority": "critical"}, {"task": "y", "priority": "standard"}],
              "sentiment": {"category": "decisive", "label": "Decisive"}}
    rec = {"title": "Q3 pricing kickoff", "durationSeconds": 2820.0, "rollingSummary": "s", "transcript": "A hi",
           "transcriptLanguage": "en", "reportJSONData": base64.b64encode(json.dumps(report).encode()).decode()}
    manifest = {"formatVersion": 1}
    if share_language:
        manifest["share_language"] = share_language
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest)); z.writestr(f"meetings/{ORIGIN}.json", json.dumps(rec))
    return buf.getvalue()


def _share(client, user, body):
    r = client.post("/v1/shares", content=body, headers={**user["headers"], **HDRS})
    assert r.status_code == 200, r.text
    return r.json()["url"].rsplit("/", 1)[1]


def test_render_is_a_1200x630_png_in_every_served_language():
    for lang in (None, "es", "fr", "ja"):
        png = render_card({"title": "Kickoff", "date": "2026-08-24", "duration_seconds": 600, "attendees": 2,
                           "action_count": 3, "urgent_count": 1, "sentiment": "Tense", "summary_line": "Short.",
                           "transcript_included": True, "has_report": True, "share_language": lang, "source_language": "en"})
        img = Image.open(io.BytesIO(png))
        assert img.size == (1200, 630) and img.format == "PNG"


def test_translated_card_differs_from_plain_and_long_titles_do_not_overflow():
    base = {"title": "A" * 400, "date": None, "duration_seconds": None, "attendees": 0, "action_count": 0,
            "urgent_count": 0, "sentiment": None, "summary_line": "", "transcript_included": False, "has_report": False}
    plain = render_card({**base, "share_language": None})
    translated = render_card({**base, "share_language": "es", "source_language": "en"})
    assert plain != translated and Image.open(io.BytesIO(plain)).size == (1200, 630)


def test_card_route_renders_once_caches_and_dies_with_the_share(client, pro_user, tmp_db_path):
    token = _share(client, pro_user, _bundle("es"))
    r = client.get(f"/s/{token}/card.png")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert Image.open(io.BytesIO(r.content)).size == (1200, 630)
    storage = sqlite3.connect(tmp_db_path).execute(
        "SELECT storage_path FROM meeting_shares WHERE token=?", (token,)).fetchone()[0]
    side = Path(storage + ".card.png")
    assert side.exists()
    mtime = side.stat().st_mtime
    assert client.get(f"/s/{token}/card.png").status_code == 200 and side.stat().st_mtime == mtime  # cached
    assert client.head(f"/s/{token}/card.png").status_code == 200
    assert client.get("/s/notatoken/card.png").status_code == 410


def test_og_image_points_at_the_card_only_when_the_flag_is_on(client, pro_user):
    from app.main import app
    token = _share(client, pro_user, _bundle())
    share = app.state.remote_configs.setdefault("client-config", {}).setdefault("share", {})
    prev = share.get("dynamic_card")
    try:
        share["dynamic_card"] = False
        assert f"/s/{token}/card.png" not in client.get(f"/s/{token}").text
        share["dynamic_card"] = True
        page = client.get(f"/s/{token}").text
        assert f"<meta property='og:image' content='https://share.shouldersurf.com/s/{token}/card.png'>" in page
    finally:
        if prev is None:
            share.pop("dynamic_card", None)
        else:
            share["dynamic_card"] = prev
