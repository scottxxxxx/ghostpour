"""Read a `.shouldersurf` bundle for the hosted share page.

Spec: SS's AudioRoutingPrototype/docs/SHOULDERSURF_BUNDLE_FORMAT.md
(formatVersion 1, 2026-08-22). A real deflate zip: manifest.json always,
project.json for project bundles, meetings/<ORIGIN_UUID>.json one per
meeting, media/ and generated_files/ opt-in. Unknown entries are ignored;
the format is additive.

Two traps, both verified by SS on a real record from Scott's iPad and
reproduced here on purpose rather than "fixed":

1. Dates are Doubles of seconds since 2001-01-01 (Swift's
   .deferredToDate, the Apple reference date), NOT unix epoch and NOT
   ISO 8601. unix = value + 978307200.
2. `reportJSONData` is a Swift Data, so it arrives as a base64 STRING
   whose decoded bytes are the report JSON. Decode, then parse. The
   meeting record is camelCase, the report inside it is snake_case
   (it came from GP), and neither is normalised.

The transcript is opt-in and usually absent; a bundle without one is
normal. `reportHTML`, when present, is a complete standalone HTML
document and is what SS renders itself, so it is the preferred page.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

APPLE_REFERENCE_EPOCH_OFFSET = 978307200  # 2001-01-01T00:00:00Z in unix seconds


def apple_date(value) -> datetime | None:
    """Seconds since 2001-01-01 -> aware UTC datetime. None for anything
    that is not a number: a bad value is a missing date, never a crash."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=float(value))
    except (OverflowError, ValueError):
        return None


def decode_report(report_json_data) -> dict | None:
    """base64 string -> bytes -> JSON dict. None when absent or malformed."""
    if not isinstance(report_json_data, str) or not report_json_data:
        return None
    try:
        return json.loads(base64.b64decode(report_json_data, validate=False).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


# Bounds on what we will decompress. Raised by CQ 2026-08-22 after SS found
# the same shape in their own reader and fixed it.
#
# This page unzips bytes that arrived from a client. The upload is
# authenticated, so it is not open to the world, but an authenticated
# client is not a trusted one, and the blast radius is not the same as
# SS's: a client-side OOM kills one person's app, a server-side one takes
# GhostPour down for everyone, including every CQ call that proxies
# through us. A deflate bomb is a few kilobytes on the wire and unbounded
# in RAM, so a cheap upload buying a whole-process death is a bad trade.
#
# Sized against reality rather than guessed: SS measured real bundles at
# 275 KB to 36.9 MB, and the big part of that is audio and images, which
# this reader NEVER opens. All we read is manifest.json, project.json and
# meetings/*.json, so even a 21-meeting project bundle is small here.
MAX_ENTRIES = 10_000
MAX_ENTRY_BYTES = 32 * 1024 * 1024
MAX_TOTAL_READ_BYTES = 128 * 1024 * 1024
_CHUNK = 64 * 1024


class ShareBundleTooLarge(Exception):
    """The archive is or claims to be past what we will decompress."""


def read_bundle(archive: bytes) -> dict:
    """Parse the zip into {manifest, project, meetings:[...]}. Each meeting
    carries the raw record plus `report` (decoded) and `started_at`
    (aware datetime or None). Never raises on a well-formed zip with odd
    contents; raises zipfile.BadZipFile on a non-zip and
    ShareBundleTooLarge on one that would cost more memory than a share
    page is worth."""
    out = {"manifest": None, "project": None, "meetings": []}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        if len(zf.infolist()) > MAX_ENTRIES:
            raise ShareBundleTooLarge(f"{len(zf.infolist())} entries")
        budget = [MAX_TOTAL_READ_BYTES]
        names = set(zf.namelist())
        if "manifest.json" in names:
            out["manifest"] = _json(zf, "manifest.json", budget)
        if "project.json" in names:
            out["project"] = _json(zf, "project.json", budget)
        for name in sorted(names):
            if name.startswith("meetings/") and name.endswith(".json") and name.count("/") == 1:
                rec = _json(zf, name, budget)
                if not isinstance(rec, dict):
                    continue
                out["meetings"].append({
                    "origin_id": name[len("meetings/"):-len(".json")],
                    "record": rec,
                    "report": decode_report(rec.get("reportJSONData")),
                    "started_at": apple_date(rec.get("date")),
                })
    return out


def _read_bounded(zf: zipfile.ZipFile, name: str, budget: list[int]) -> bytes | None:
    """Decompress one entry, stopping the moment it goes past its bounds.

    The check runs INSIDE the read loop, not after the entry finishes.
    SS's point and it is the whole thing: checking a completed entry means
    the bomb is already in memory by the time you object to it, which is
    exactly what you were trying to prevent. `ZipExtFile.read(n)`
    decompresses only about n bytes, so this really is streaming.

    The declared size is checked first because it is free, and NOT trusted,
    because a crafted central directory can lie about it. It is a fast
    path, not the bound.
    """
    try:
        if zf.getinfo(name).file_size > MAX_ENTRY_BYTES:
            return None
    except KeyError:
        return None
    buf = bytearray()
    cap = min(MAX_ENTRY_BYTES, budget[0])
    with zf.open(name) as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            buf += chunk
            if len(buf) > cap:
                return None
    budget[0] -= len(buf)
    return bytes(buf)


def _json(zf: zipfile.ZipFile, name: str, budget: list[int]):
    raw = _read_bounded(zf, name, budget)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


# --- the page --------------------------------------------------------------

def _esc(s) -> str:
    return str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def _duration(sec) -> str:
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(sec, 3600); m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


def render_share_page(bundle: dict, *, card_title: str, card_desc: str, transcript_included: bool,
                      expires_at: str, og_image_url: str | None = None,
                      app_store_id: str | None = None, share_url: str | None = None,
                      icon_url: str | None = None) -> str:
    """The hosted page for a recipient without the app (Variant A: the
    whole meeting). Card tags for iMessage and every other messenger;
    noindex; `reportHTML` when the record carries it, in a sandboxed
    frame (no scripts run); otherwise a page built from the decoded
    report; the transcript, when present and included, behind a
    tap-to-reveal. Never raises on odd content: every field is optional."""
    meetings = bundle.get("meetings") or []
    parts = []
    for m in meetings:
        rec = m.get("record") or {}; rep = m.get("report") or {}
        title = rec.get("title") or (rep.get("header") or {}).get("title") or card_title
        when = m.get("started_at"); when_s = when.strftime("%B %-d, %Y, %-I:%M %p UTC") if when else ""
        dur = _duration(rec.get("durationSeconds"))
        meta = " · ".join(x for x in (when_s, dur) if x)
        body = []
        html_doc = rec.get("reportHTML") if isinstance(rec.get("reportHTML"), str) else ""
        if html_doc.strip():
            body.append(f"<iframe sandbox=\"\" srcdoc=\"{_esc(html_doc)}\" style=\"width:100%;min-height:70vh;border:0;border-radius:12px;background:#fff\" title=\"Meeting report\"></iframe>")
        else:
            header = rep.get("header") or {}
            summary = header.get("summary") or rec.get("rollingSummary") or ""
            if summary:
                body.append(f"<p class='summary'>{_esc(summary)}</p>")
            attendees = header.get("attendees") or []
            if attendees:
                body.append("<p class='k'>With</p><p>" + ", ".join(_esc(a) for a in attendees) + "</p>")
            sent = rep.get("sentiment") or {}
            if sent.get("label") or rec.get("sentimentLabel"):
                body.append(f"<p class='k'>Sentiment</p><p>{_esc(sent.get('label') or rec.get('sentimentLabel'))}</p>")
            actions = [a for a in (rep.get("actions") or []) if isinstance(a, dict)]
            if actions:
                body.append("<p class='k'>Action items</p><ul>" + "".join(
                    f"<li><b>{_esc(a.get('task'))}</b>" + (f" <span class='dim'>{_esc(a.get('owner'))}</span>" if a.get("owner") else "")
                    + (f" <span class='dim'>· {_esc(a.get('deadline'))}</span>" if a.get("deadline") else "") + "</li>"
                    for a in actions) + "</ul>")
            decisions = [d for d in (rep.get("decisions") or []) if isinstance(d, dict)]
            if decisions:
                body.append("<p class='k'>Decisions</p><ul>" + "".join(f"<li>{_esc(d.get('title') or d.get('text') or d)}</li>" for d in decisions) + "</ul>")
            oq = [q for q in (rep.get("open_questions") or []) if isinstance(q, dict)]
            if oq:
                body.append("<p class='k'>Open questions</p><ul>" + "".join(f"<li>{_esc(q.get('question') or q.get('text') or q)}</li>" for q in oq) + "</ul>")
            if not body:
                body.append("<p class='dim'>This meeting was shared without a report.</p>")
        transcript = rec.get("transcript") if isinstance(rec.get("transcript"), str) else ""
        if transcript_included and transcript.strip():
            body.append("<details class='tx'><summary>Show transcript</summary><pre>" + _esc(transcript) + "</pre></details>")
        parts.append(f"<section><h1>{_esc(title)}</h1><p class='dim'>{_esc(meta)}</p>{''.join(body)}</section>")
    if not parts:
        parts.append(f"<section><h1>{_esc(card_title)}</h1><p class='dim'>This share holds no meeting.</p></section>")
    # og:image is the difference between the app mark and a Safari compass
    # in the iMessage bubble. Width/height let the messenger lay the card
    # out before the fetch completes; the twitter card type switches from
    # the compact thumbnail to the wide banner once there is an image.
    og_img = (
        f"<meta property='og:image' content='{_esc(og_image_url)}'>"
        f"<meta property='og:image:width' content='1200'><meta property='og:image:height' content='630'>"
        f"<meta name='twitter:image' content='{_esc(og_image_url)}'>"
    ) if og_image_url else ""
    icon = f"<link rel='apple-touch-icon' href='{_esc(icon_url)}'>" if icon_url else ""
    card_type = "summary_large_image" if og_image_url else "summary"

    # The route a recipient WITHOUT the app takes, which is the case this
    # page exists for and the one it did not serve until 2026-08-23.
    #
    # Two mechanisms on purpose, because they cover different people:
    #
    # `apple-itunes-app` is Apple's own banner. iOS Safari renders it
    # natively at the top of the page and decides the verb itself: "Open"
    # when the app is installed, "Get" when it is not. We cannot detect
    # installation from a web page and must not try; this is the one
    # thing that can. `app-argument` hands this share's URL to the app on
    # Open, so the app lands on THIS meeting rather than on its home
    # screen.
    #
    # The visible link is for everyone Safari's banner does not reach:
    # Chrome on iOS, Android, and every desktop browser, which is where a
    # link pasted into Slack actually gets opened.
    #
    # Absent id = neither appears. No dead App Store link on a page a
    # stranger opens, and the page is otherwise unchanged.
    banner = get_app = ""
    if app_store_id:
        arg = f",app-argument={_esc(share_url)}" if share_url else ""
        banner = f"<meta name='apple-itunes-app' content='app-id={_esc(app_store_id)}{arg}'>"
        store = f"https://apps.apple.com/app/id{_esc(app_store_id)}"
        get_app = (f"<p class='get'><a href='{store}'>Open in Shoulder Surf</a>"
                   "<span class='dim'> or read it here</span></p>")

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{_esc(card_title)}</title><meta name='robots' content='noindex'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta property='og:title' content='{_esc(card_title)}'><meta property='og:description' content='{_esc(card_desc)}'>"
        f"<meta property='og:type' content='article'><meta property='og:site_name' content='Shoulder Surf'>"
        + (f"<meta property='og:url' content='{_esc(share_url)}'>" if share_url else "") + og_img +
        f"<meta name='twitter:card' content='{card_type}'><meta name='twitter:title' content='{_esc(card_title)}'><meta name='twitter:description' content='{_esc(card_desc)}'>"
        + banner + icon +
        "<style>body{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:720px;margin:0 auto;padding:1rem;color:#1a1a1a;background:#fafaf8}"
        "h1{font-size:1.4rem;margin:.5rem 0}.dim{color:#777}.k{font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;color:#888;margin:1.2rem 0 .2rem}"
        ".summary{font-size:1.05rem}ul{padding-left:1.2rem}.tx pre{white-space:pre-wrap;background:#f1f1ee;padding:1rem;border-radius:8px}"
        ".foot{margin-top:2rem;font-size:.8rem;color:#888}"
        ".get{margin:.25rem 0 1rem}.get a{display:inline-block;background:#1a1a1a;color:#fff;text-decoration:none;"
        "padding:.5rem .9rem;border-radius:8px;font-size:.9rem}</style></head><body>"
        + get_app + "".join(parts) +
        f"<p class='foot'>Shared from Shoulder Surf. This link stops working on {_esc(expires_at[:10])}.</p>"
        "</body></html>"
    )
