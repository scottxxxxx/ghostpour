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

import logging

logger = logging.getLogger("ghostpour.share_bundle")

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


def _md_inline(t: str) -> str:
    """Inline markdown on ALREADY-ESCAPED text: bold, italic, code, links.
    Runs after _esc so it can never introduce unescaped HTML."""
    import re as _re
    t = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = _re.sub(r"__(.+?)__", r"<strong>\1</strong>", t)
    t = _re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<em>\1</em>", t)
    t = _re.sub(r"`([^`]+?)`", r"<code>\1</code>", t)
    # [text](http...) — only http(s), href re-escaped so quotes can't break out
    def _link(m):
        url = m.group(2)
        if not (url.startswith("http://") or url.startswith("https://")):
            return m.group(0)
        return f"<a href=\"{_esc(url)}\" target=\"_blank\" rel=\"noopener nofollow\">{m.group(1)}</a>"
    t = _re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, t)
    return t


def render_markdown(text: str) -> str:
    """A small, SAFE markdown -> HTML for the share page's summary (Scott
    2026-08-24: the model writes headings, bold and bullets and the page
    was showing the raw characters). HTML is escaped FIRST, so only the
    formatting we add is live; no library, no arbitrary HTML."""
    import re as _re
    if not text or not str(text).strip():
        return ""
    out, in_ul = [], False
    for raw in _esc(text).split("\n"):
        line = raw.rstrip()
        h = _re.match(r"^(#{1,6})\s+(.*)$", line)
        b = _re.match(r"^\s*(?:[-*\u2022]|\d+\.)\s+(.*)$", line)
        if b:
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{_md_inline(b.group(1))}</li>")
            continue
        if in_ul:
            out.append("</ul>"); in_ul = False
        if h:
            lvl = min(len(h.group(1)) + 2, 6)
            out.append(f"<h{lvl}>{_md_inline(h.group(2))}</h{lvl}>")
        elif line.strip():
            out.append(f"<p>{_md_inline(line)}</p>")
    if in_ul:
        out.append("</ul>")
    return "".join(out)


def _duration(sec) -> str:
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(sec, 3600); m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


# Audio (Scott 2026-08-24: playable on the page, synced to the transcript).
# The bundle carries media/<ORIGIN>/audio/<name>.m4a, opt-in, AAC 16 kHz
# mono 32 kbps, so ~4 KB/s: a 2 h meeting is ~29 MB. The page reader
# still never inflates audio; the audio ROUTE extracts one entry to a
# sidecar file next to the archive on first request (bounded), and the
# sidecar is served with Range support so scrubbing works. Sidecars are
# deleted with the share.
MAX_AUDIO_ENTRY_BYTES = 64 * 1024 * 1024


def list_audio_entries(archive_bytes_or_path) -> dict[str, list[str]]:
    """{origin_id: [entry names in name order]} for every audio entry in the
    zip. Names only: nothing is inflated here."""
    import zipfile
    out: dict[str, list[str]] = {}
    try:
        zf = zipfile.ZipFile(archive_bytes_or_path if not isinstance(archive_bytes_or_path, (bytes, bytearray))
                             else __import__("io").BytesIO(archive_bytes_or_path))
    except zipfile.BadZipFile:
        return out
    with zf:
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) == 4 and parts[0] == "media" and parts[2] == "audio" and name.lower().endswith(".m4a"):
                out.setdefault(parts[1], []).append(name)
    for k in out:
        out[k].sort()
    return out


def flat_audio_entries(audio_by_origin: dict[str, list[str]]) -> list[tuple[str, str]]:
    """The ONE ordering both the route and the page use for `n`: origins
    sorted, then names sorted within each. SS's model (2026-08-24): a
    meeting carries at most one audio file, so a bundle with several is
    a multi-meeting bundle and each file belongs to its own origin's
    segments; never stitch across files."""
    return [(origin, name) for origin in sorted(audio_by_origin) for name in audio_by_origin[origin]]


def list_image_entries(archive_path) -> dict[str, list[str]]:
    """{origin_id: [entry names in name order]} for media/<origin>/images/*."""
    import zipfile
    out: dict[str, list[str]] = {}
    try:
        zf = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile:
        return out
    with zf:
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) == 4 and parts[0] == "media" and parts[2] == "images" and parts[3]:
                out.setdefault(parts[1], []).append(name)
    for k in out:
        out[k].sort()
    return out


def flat_image_entries(images_by_origin: dict[str, list[str]]) -> list[tuple[str, str]]:
    """The ONE ordering both the route and the page use for image n."""
    return [(o, n) for o in sorted(images_by_origin) for n in images_by_origin[o]]


def list_image_counts(archive_path) -> dict[str, int]:
    """{origin_id: count} of media/<origin>/images/* entries. Names only."""
    import zipfile
    out: dict[str, int] = {}
    try:
        zf = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile:
        return out
    with zf:
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) == 4 and parts[0] == "media" and parts[2] == "images" and parts[3]:
                out[parts[1]] = out.get(parts[1], 0) + 1
    return out


def extract_audio_sidecar(archive_path: str, entry_name: str, sidecar_path: str) -> bool:
    """Inflate ONE audio entry to `sidecar_path`, streaming, with the same
    in-loop bound discipline as `_json` (a claimed size is not a size).
    Returns False (and leaves no partial file) when the entry is missing,
    oversized, or unreadable."""
    import os, zipfile
    from pathlib import Path
    tmp = sidecar_path + ".part"
    try:
        with zipfile.ZipFile(archive_path) as zf:
            try:
                info = zf.getinfo(entry_name)
            except KeyError:
                return False
            if info.file_size > MAX_AUDIO_ENTRY_BYTES:
                return False
            written = 0
            with zf.open(info) as src, open(tmp, "wb") as dst:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_AUDIO_ENTRY_BYTES:
                        raise ShareBundleTooLarge(entry_name)
                    dst.write(chunk)
        os.replace(tmp, sidecar_path)
        return True
    except (zipfile.BadZipFile, ShareBundleTooLarge, OSError):
        Path(tmp).unlink(missing_ok=True)
        return False


# Language labels for the picker. The picker offers ONLY Original plus the
# languages the sender already translated in the app (Scott 2026-08-24):
# no translating from the web view, no picker when nothing was translated.
LANG_LABELS = {"en": "English", "es": "Español", "fr": "Français", "ja": "日本語",
               "de": "Deutsch", "pt": "Português", "it": "Italiano", "zh": "中文", "ko": "한국어"}

# "What this is" on the card (Scott 2026-08-24), composed from the bundle
# BYTES, never from a header: report, transcript, audio, photos.
CONTENTS_WORDS = {
    "en": {"report": "Report", "transcript": "Transcript", "audio": "Audio", "photos": "{n} photos", "photo": "1 photo"},
    "es": {"report": "Informe", "transcript": "Transcripción", "audio": "Audio", "photos": "{n} fotos", "photo": "1 foto"},
    "fr": {"report": "Rapport", "transcript": "Transcription", "audio": "Audio", "photos": "{n} photos", "photo": "1 photo"},
    "ja": {"report": "レポート", "transcript": "文字起こし", "audio": "音声", "photos": "写真{n}枚", "photo": "写真1枚"},
}


DOWNLOAD_STRINGS = {
    "en": {"badge_locale": "en-us", "badge_alt": "Download on the App Store",
           "qr_alt": "QR code to download Shoulder Surf",
           "desktop": "On a desktop? Point your iPhone camera here to download."},
    "es": {"badge_locale": "es-es", "badge_alt": "Descárgalo en el App Store",
           "qr_alt": "Código QR para descargar Shoulder Surf",
           "desktop": "¿Estás en un ordenador? Apunta la cámara de tu iPhone aquí para descargarla."},
    "fr": {"badge_locale": "fr-fr", "badge_alt": "Télécharger dans l'App Store",
           "qr_alt": "QR code pour télécharger Shoulder Surf",
           "desktop": "Sur un ordinateur ? Pointez l'appareil photo de votre iPhone ici pour télécharger."},
    "ja": {"badge_locale": "ja-jp", "badge_alt": "App Storeでダウンロード",
           "qr_alt": "Shoulder SurfをダウンロードするQRコード",
           "desktop": "パソコンでご覧ですか？iPhoneのカメラをここにかざしてダウンロードできます。"},
}


def _lang_label(tag: str) -> str:
    return LANG_LABELS.get(tag.split("-")[0].lower(), tag)


def renditions_of(rec: dict) -> list[dict]:
    """The sender's stored translations, as emitted by SS (f318ab0):
    transcriptRenditions[{lang, engine_version?, created_at?, transcript,
    summary?, report_html?}], full text. Malformed entries are dropped."""
    out = []
    for r in (rec.get("transcriptRenditions") or []):
        if isinstance(r, dict) and isinstance(r.get("lang"), str) and r["lang"].strip():
            out.append(r)
    return out


def _rendition_lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _norm(t: str) -> str:
    return " ".join((t or "").split()).lower()


def _strip_label(line: str) -> str:
    """'[Maureen Bowyer] text' or 'Maureen Bowyer: text' -> 'text'."""
    line = line.strip()
    if line.startswith("[") and "]" in line:
        return line[line.index("]") + 1:].strip()
    return line


def group_segments_by_transcript_lines(transcript: str, segments: list) -> list[list[int]] | None:
    """SS's `transcript` text has one line per SPOKEN TURN, and a turn can
    span several transcriptSegments. Map each text line to the run of
    consecutive segment indexes whose texts concatenate into it (measured
    on Scott's 2026-08-24 share: 41 lines over 44 segments). Returns one
    index list per text line, or None when the texts do not line up."""
    kept = [(i, seg) for i, seg in enumerate(segments)
            if isinstance(seg, dict) and isinstance(seg.get("text"), str) and seg["text"].strip()]
    lines = [_strip_label(l) for l in (transcript or "").splitlines() if l.strip()]
    if not lines or not kept:
        return None
    groups, pos = [], 0
    for line in lines:
        target = _norm(line)
        acc, idxs = "", []
        while pos < len(kept):
            i, seg = kept[pos]
            cand = _norm((acc + " " + seg["text"]).strip())
            if not target.startswith(cand):
                break
            acc, pos = (acc + " " + seg["text"]).strip(), pos + 1
            idxs.append(i)
            if cand == target:
                break
        if not idxs or _norm(acc) != target:
            return None
        groups.append(idxs)
    return groups if pos == len(kept) else None


def align_rendition(rendition: dict, segments: list, transcript: str = "") -> list[tuple[list[int], str]] | None:
    """Rendition line i <-> the segment group of transcript line i (or
    segment i when the rendition has one line per segment). Returns
    [(segment indexes, translated line)] or None: a mismatch is shown as
    plain text IN PLACE, never guessed onto the wrong lines."""
    lines = _rendition_lines(rendition.get("transcript") if isinstance(rendition.get("transcript"), str) else "")
    kept = [i for i, seg in enumerate(segments)
            if isinstance(seg, dict) and isinstance(seg.get("text"), str) and seg["text"].strip()]
    if not lines:
        return None
    if len(lines) == len(kept):
        return [([i], _strip_label(l)) for i, l in zip(kept, lines)]
    groups = group_segments_by_transcript_lines(transcript, segments)
    if groups is not None and len(groups) == len(lines):
        return [(g, _strip_label(l)) for g, l in zip(groups, lines)]
    logger.warning("share_rendition_misaligned", extra={
        "lang": rendition.get("lang"), "lines": len(lines), "segments": len(kept),
        "transcript_lines": len([l for l in (transcript or "").splitlines() if l.strip()])})
    return None


def _times(seg: dict) -> tuple[float, float]:
    try:
        s0 = float(seg.get("sessionTimeOffset") or 0.0)
        e0 = float(seg.get("endTimeOffset") or s0)
    except (TypeError, ValueError):
        s0, e0 = 0.0, 0.0
    return s0, e0


def _segments_html(segments: list, aligned: dict[str, list[tuple[list[int], str]]]) -> str:
    """The ORIGINAL view: one <p class='seg'> per segment with timing.
    Then, per aligned rendition, a TRANSLATED view of the same window:
    one <p class='seg'> per translated line spanning its segment group's
    timing, hidden until picked. Both live inside the same window, so a
    picked language replaces the original in place and the follow-along
    highlights whichever view is showing."""
    rows = []
    for seg in segments:
        if not isinstance(seg, dict) or not isinstance(seg.get("text"), str) or not seg["text"].strip():
            continue
        s0, e0 = _times(seg)
        who = seg.get("speakerLabel")
        rows.append(
            f"<p class='seg' data-s='{s0:.2f}' data-e='{e0:.2f}'>"
            + (f"<b>{_esc(who)}</b> " if isinstance(who, str) and who.strip() else "")
            + _esc(seg["text"]) + "</p>")
    views = [f"<div class='view' data-lang=''>{''.join(rows)}</div>"]
    for lang, pairs in aligned.items():
        trows = []
        for idxs, text in pairs:
            first, last = segments[idxs[0]], segments[idxs[-1]]
            s0, _ = _times(first); _, e0 = _times(last)
            who = first.get("speakerLabel")
            trows.append(
                f"<p class='seg' data-s='{s0:.2f}' data-e='{e0:.2f}'>"
                + (f"<b>{_esc(who)}</b> " if isinstance(who, str) and who.strip() else "")
                + _esc(text) + "</p>")
        views.append(f"<div class='view' data-lang='{_esc(lang)}' style='display:none'>{''.join(trows)}</div>")
    return "".join(views)


def _picker_html(langs: list[str], default: str | None) -> str:
    if not langs:
        return ""
    opts = "".join(
        f"<option value='{_esc(l)}'{' selected' if default and l.split('-')[0].lower() == default.split('-')[0].lower() else ''}>{_esc(_lang_label(l))}</option>"
        for l in langs)
    return ("<p class='k'>Show transcript in <select class='lang'>"
            "<option value=''>Original</option>" + opts + "</select></p>")


def contents_descriptor(rec: dict, audio_count: int, image_count: int, lang: str | None) -> str:
    words = CONTENTS_WORDS.get((lang or "en").split("-")[0].lower(), CONTENTS_WORDS["en"])
    parts = []
    if rec.get("reportHTML") or rec.get("reportJSONData"):
        parts.append(words["report"])
    if isinstance(rec.get("transcript"), str) and rec["transcript"].strip():
        parts.append(words["transcript"])
    if audio_count:
        parts.append(words["audio"])
    if image_count == 1:
        parts.append(words["photo"])
    elif image_count > 1:
        parts.append(words["photos"].format(n=image_count))
    return " · ".join(parts)


_LIGHTBOX_HTML = (
    "<div class='lb' aria-hidden='true'><button class='close' aria-label='Close'>&times;</button>"
    "<button class='prev' aria-label='Previous'>&#8249;</button>"
    "<img alt=''><button class='next' aria-label='Next'>&#8250;</button>"
    "<div class='count'></div></div>"
)

# Photo viewer overlay: opens on top of the page (never replacing it),
# cycles with the arrows or the keyboard, closes on X / Escape / backdrop
# (Scott 2026-08-24).
_LIGHTBOX_JS = (
    "<script>(function(){var lb=document.querySelector('.lb');if(!lb)return;"
    "var thumbs=Array.prototype.slice.call(document.querySelectorAll('.thumb'));if(!thumbs.length)return;"
    "var img=lb.querySelector('img'),cnt=lb.querySelector('.count'),i=0;"
    "function show(n){i=(n+thumbs.length)%thumbs.length;img.src=thumbs[i].dataset.full;cnt.textContent=(i+1)+' / '+thumbs.length;}"
    "function open(n){show(n);lb.classList.add('on');lb.setAttribute('aria-hidden','false');}"
    "function close(){lb.classList.remove('on');lb.setAttribute('aria-hidden','true');img.src='';}"
    "thumbs.forEach(function(t,n){t.addEventListener('click',function(){open(n);});});"
    "lb.querySelector('.close').addEventListener('click',close);"
    "lb.querySelector('.prev').addEventListener('click',function(e){e.stopPropagation();show(i-1);});"
    "lb.querySelector('.next').addEventListener('click',function(e){e.stopPropagation();show(i+1);});"
    "lb.addEventListener('click',function(e){if(e.target===lb)close();});"
    "document.addEventListener('keydown',function(e){if(!lb.classList.contains('on'))return;"
    "if(e.key==='Escape')close();else if(e.key==='ArrowLeft')show(i-1);else if(e.key==='ArrowRight')show(i+1);});"
    "})();</script>"
)


# Sync is scoped per <section>: each meeting's player drives only that
# meeting's segments, highlights and auto-scrolls INSIDE the transcript
# window (never the page), and a tap on a line seeks that meeting's
# player. The picker swaps each line's text from its data-tr-<lang>.
_PLAYER_JS = (
    "<script>(function(){document.querySelectorAll('section').forEach(function(sec){"
    "var a=sec.querySelector('audio.sa');var box=sec.querySelector('.segs');"
    "var sel=sec.querySelector('select.lang');var sum=sec.querySelector('.summary');var S=[];"
    "function pick(lang){var views=sec.querySelectorAll('.view');var shown=null;views.forEach(function(v){var on=(v.getAttribute('data-lang')||'')===(lang||'');v.style.display=on?'':'none';if(on)shown=v;});"
    "if(!shown&&views.length){views[0].style.display='';shown=views[0];}"
    "S=shown?Array.prototype.slice.call(shown.querySelectorAll('p.seg')):[];if(cur){cur.classList.remove('on');cur=null;}"
    "if(sum){var sv=lang?sum.getAttribute('data-sum-'+lang):null;sum.innerHTML=sv||sum.getAttribute('data-sum-orig');}}"
    "var cur=null;pick(sel?sel.value:'');if(sel){sel.addEventListener('change',function(){pick(sel.value);});}"
    "if(!a)return;a.addEventListener('timeupdate',function(){var t=a.currentTime,hit=null;"
    "for(var i=0;i<S.length;i++){var s=+S[i].dataset.s,e=+S[i].dataset.e;if(t>=s&&(t<e||(e<=s&&(i+1>=S.length||t<+S[i+1].dataset.s)))){hit=S[i];break;}}"
    "if(hit!==cur){if(cur)cur.classList.remove('on');cur=hit;if(cur){cur.classList.add('on');var d=cur.closest('details');if(d&&!d.open)d.open=true;"
    "if(box){box.scrollTop=Math.max(0,cur.offsetTop-box.offsetTop-box.clientHeight/2+cur.clientHeight/2);}}}});"
    "sec.addEventListener('click',function(ev){var p=ev.target.closest('p.seg');if(p&&sec.contains(p)){a.currentTime=+p.dataset.s;a.play();}});});})();</script>"
)


def render_share_page(bundle: dict, *, card_title: str, card_desc: str, transcript_included: bool,
                      expires_at: str, og_image_url: str | None = None,
                      app_store_id: str | None = None, share_url: str | None = None,
                      icon_url: str | None = None, audio_by_origin: dict[str, list[str]] | None = None,
                      share_language: str | None = None, images_by_origin: dict[str, int] | None = None,
                      qr_url: str | None = None, images_by_origin_names: dict[str, list[str]] | None = None) -> str:
    """The hosted page for a recipient without the app (Variant A: the
    whole meeting). Card tags for iMessage and every other messenger;
    noindex; `reportHTML` when the record carries it, in a sandboxed
    frame (no scripts run); otherwise a page built from the decoded
    report; the transcript, when present and included, behind a
    tap-to-reveal. Never raises on odd content: every field is optional."""
    meetings = bundle.get("meetings") or []
    manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), dict) else {}
    if share_language is None and isinstance(manifest.get("share_language"), str):
        share_language = manifest["share_language"]
    parts = []
    contents_line = ""
    for m in meetings:
        rec = m.get("record") or {}; rep = m.get("report") or {}
        # card_title is X-Share-Title = the client's displayTitle: the most
        # current, authoritatively-derived, and (on a translated share)
        # localized title. For a SINGLE-meeting share it IS this meeting's
        # display title, so prefer it over the bundle's raw rec.title (the
        # untranslated stored title, or a bare date on a report-less meeting
        # from before SS's derivation fix). For a MULTI-meeting bundle
        # card_title is one share-level title, so each section keeps its own
        # meeting title. (Scott 2026-08-24.)
        _mtitle = rec.get("title") or (rep.get("header") or {}).get("title")
        title = (card_title or _mtitle) if len(meetings) == 1 else (_mtitle or card_title)
        when = m.get("started_at"); when_s = when.strftime("%B %-d, %Y, %-I:%M %p UTC") if when else ""
        dur = _duration(rec.get("durationSeconds"))
        meta = " · ".join(x for x in (when_s, dur) if x)
        contents = contents_descriptor(
            rec, len((audio_by_origin or {}).get(m.get("origin_id") or "", [])),
            int((images_by_origin or {}).get(m.get("origin_id") or "", 0)), share_language)
        if contents and not contents_line:
            contents_line = contents
        if contents:
            meta = " · ".join(x for x in (meta, contents) if x)
        body = []
        html_doc = rec.get("reportHTML") if isinstance(rec.get("reportHTML"), str) else ""
        if html_doc.strip():
            body.append(f"<iframe sandbox=\"\" srcdoc=\"{_esc(html_doc)}\" style=\"width:100%;min-height:70vh;border:0;border-radius:12px;background:#fff\" title=\"Meeting report\"></iframe>")
        else:
            header = rep.get("header") or {}
            summary = header.get("summary") or rec.get("rollingSummary") or ""
            if summary:
                rendered = render_markdown(summary)
                # Each stored value is server-rendered safe HTML; the picker
                # swaps it in with innerHTML. _esc into the attribute so the
                # markup survives as an attribute value and decodes back.
                sum_attrs = "".join(
                    f" data-sum-{_esc(r['lang'])}='{_esc(render_markdown(r['summary']))}'"
                    for r in renditions_of(rec)
                    if isinstance(r.get("summary"), str) and r["summary"].strip())
                body.append(f"<div class='summary' data-sum-orig='{_esc(rendered)}'{sum_attrs}>{rendered}</div>")
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
        # Audio players: one per audio entry, in name order, served by
        # /s/{token}/audio/{n} where n indexes this meeting's entries.
        origin = m.get("origin_id") or ""
        audio_names = (audio_by_origin or {}).get(origin, [])
        if audio_names and share_url:
            flat = flat_audio_entries(audio_by_origin or {})
            for name in audio_names:
                n = flat.index((origin, name))
                body.append(
                    "<p class='k'>Recording</p>"
                    f"<audio class='sa' controls preload='metadata' src='{_esc(share_url)}/audio/{n}'></audio>")
        transcript = rec.get("transcript") if isinstance(rec.get("transcript"), str) else ""
        segments = rec.get("transcriptSegments") if isinstance(rec.get("transcriptSegments"), list) else []
        rends = renditions_of(rec)
        aligned = {}
        plain = []
        for r in rends:
            pairs = align_rendition(r, segments, transcript)
            if pairs is not None:
                aligned[r["lang"]] = pairs
            elif isinstance(r.get("transcript"), str) and r["transcript"].strip():
                plain.append(r)
        seg_html = _segments_html(segments, aligned) if (transcript_included and segments) else ""
        if seg_html or (transcript_included and transcript.strip()):
            langs = [r["lang"] for r in rends if r["lang"] in aligned or any(pr is r for pr in plain)]
            picker = _picker_html(langs, share_language)
            # A rendition that could not be aligned still replaces the
            # original IN PLACE (same window), just without timing.
            plain_views = "".join(
                f"<div class='view' data-lang='{_esc(r['lang'])}' style='display:none'><pre class='rend'>{_esc(r['transcript'])}</pre></div>"
                for r in plain)
            orig_view = seg_html if seg_html else f"<div class='view' data-lang=''><pre class='orig-plain'>{_esc(transcript)}</pre></div>"
            body.append("<details class='tx'" + (" open" if audio_names else "") +
                        f" data-origin='{_esc(origin)}'><summary>Show transcript</summary>" + picker +
                        "<div class='segs'>" + orig_view + plain_views + "</div></details>")
        # Photos shared with the meeting (Scott 2026-08-24: they were in the
        # bundle and never on the page). Served by /s/{token}/image/{n}.
        n_images = int((images_by_origin or {}).get(origin, 0))
        if n_images and share_url:
            flat_imgs = flat_image_entries(images_by_origin_names or {})
            thumbs = "".join(
                f"<button type='button' class='thumb' data-full='{_esc(share_url)}/image/{n}'><img src='{_esc(share_url)}/image/{n}' loading='lazy' alt=''></button>"
                for n, (o, _name) in enumerate(flat_imgs) if o == origin)
            if thumbs:
                body.append(f"<p class='k'>Photos</p><div class='gallery'>{thumbs}</div>")

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
    if contents_line:
        card_desc = f"{contents_line} · {card_desc}" if card_desc else contents_line

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
    banner = get_app = download = ""
    if app_store_id:
        arg = f",app-argument={_esc(share_url)}" if share_url else ""
        banner = f"<meta name='apple-itunes-app' content='app-id={_esc(app_store_id)}{arg}'>"
        store = f"https://apps.apple.com/app/id{_esc(app_store_id)}"
        get_app = (f"<p class='get'><a href='{store}'>Open in Shoulder Surf</a>"
                   "<span class='dim'> or read it here</span></p>")
        # The website's download block, on every hosted meeting (Scott
        # 2026-08-24): Apple's official badge for a phone in hand, the QR
        # for someone reading on a desktop. Localized to the shared
        # language; the badge art comes from Apple's badge service in the
        # matching locale, the QR from the website (served url).
        dl = DOWNLOAD_STRINGS.get((share_language or "en").split("-")[0].lower(), DOWNLOAD_STRINGS["en"])
        badge = (f"https://tools.applemediaservices.com/api/badges/download-on-the-app-store/black/"
                 f"{dl['badge_locale']}?size=250x83")
        qr = (f"<a class='qr' href='{store}'><img src='{_esc(qr_url)}' width='120' height='120' alt='{_esc(dl['qr_alt'])}'></a>"
              f"<p class='qrtxt'>{_esc(dl['desktop'])}</p>") if qr_url else ""
        download = (f"<div class='dl'><a href='{store}' class='badge'><img src='{badge}' height='56' alt='{_esc(dl['badge_alt'])}'></a>"
                    + qr + "</div>")

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
        ".summary{font-size:1.05rem}.summary h3,.summary h4,.summary h5{margin:1rem 0 .3rem;font-size:1.05rem}.summary p{margin:.5rem 0}.summary ul{margin:.4rem 0}.summary code{background:#eee;padding:0 .25rem;border-radius:4px}ul{padding-left:1.2rem}.tx pre{white-space:pre-wrap;background:#f1f1ee;padding:1rem;border-radius:8px}"
        ".segs{background:#f1f1ee;padding:.5rem 1rem;border-radius:8px;max-height:60vh;overflow-y:auto;position:relative}.seg .tr{display:none}.seg .tr:empty{display:none}select.lang{font:inherit;padding:.15rem .4rem}.rend,.orig-plain{white-space:pre-wrap;background:#f1f1ee;padding:1rem;border-radius:8px;max-height:60vh;overflow-y:auto}.seg{margin:.15rem 0;padding:.15rem .4rem;border-radius:4px;cursor:pointer}.seg.on{background:#ffe9a8}audio.sa{width:100%;margin:.25rem 0 .75rem}"
        ".foot{margin-top:2rem;font-size:.8rem;color:#888}"
        ".get{margin:.25rem 0 1rem}.get a{display:inline-block;background:#1a1a1a;color:#fff;text-decoration:none;"
        "padding:.5rem .9rem;border-radius:8px;font-size:.9rem}"
        ".dl{display:flex;align-items:center;gap:1.25rem;flex-wrap:wrap;margin:0 0 1rem;padding:1rem 1.25rem;background:#fff;border:1px solid #e6e6e2;border-radius:14px}"
        ".gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:.5rem}.thumb{padding:0;border:0;background:none;cursor:zoom-in}.gallery img{width:100%;height:120px;object-fit:cover;border-radius:8px;display:block}"
        ".lb{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:50}.lb.on{display:flex}"
        ".lb img{max-width:92vw;max-height:86vh;object-fit:contain;border-radius:6px}"
        ".lb button{position:absolute;background:rgba(0,0,0,.45);color:#fff;border:0;border-radius:999px;width:44px;height:44px;font-size:1.5rem;line-height:1;cursor:pointer}"
        ".lb .close{top:16px;right:16px}.lb .prev{left:12px}.lb .next{right:12px}.lb .prev,.lb .next{top:50%;transform:translateY(-50%)}.lb .count{position:absolute;bottom:16px;left:0;right:0;text-align:center;color:#ccc;font-size:.9rem;background:none;width:auto;height:auto;border-radius:0}"
        ".dl .badge img{display:block}.dl .qr img{display:block;border-radius:8px}.qrtxt{margin:0;max-width:16rem;color:#555;font-size:.95rem}</style></head><body>"
        + get_app + "".join(parts) +
        f"<p class='foot'>Shared from Shoulder Surf. This link stops working on {_esc(expires_at[:10])}.</p>"
        + download + _LIGHTBOX_HTML + _PLAYER_JS + _LIGHTBOX_JS + "</body></html>"
    )
