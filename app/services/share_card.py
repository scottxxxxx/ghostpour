"""The link-preview card for a shared meeting: variant 2b, the LEDGER
(Scott's pick, Claude Design e6ee7ae8, spec relayed by SS 2026-08-24).

Rendered server-side with Pillow from the bundle bytes plus the share
row's headers; 1200x630 PNG. Inter (OFL, vendored at app/static/fonts).
Copy for the footer CTA is Social's and lands in served config; the
placeholder here is theirs until then.
"""
from __future__ import annotations

import io
import logging
import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("ghostpour.share_card")

W, H = 1200, 630
FONT_PATH = Path(__file__).resolve().parent.parent / "static" / "fonts" / "Inter.ttf"
LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "share" / "icon-512.png"

INK = "#0B0F14"; BLUE = "#0A84FF"; LABEL_BLUE = "#6FB6FF"; MUTED = "#6B7480"; CAPTION = "#8A93A0"
DIM = "#8792A0"; HAIR = "#ECEDF0"; FOOT_BG = "#F5F6F8"; FOOT_LINE = "#E4E6EA"; INSIGHT = "#3C3C43"
URGENT = "#C77700"; BOLT = "#FF9F0A"

STRINGS = {
    "en": {"report": "MEETING REPORT", "transcript": "TRANSCRIPT", "plus": " + ", "readonly": "Read-only link",
           "min": "{n} min", "people": "{n} people", "person": "1 person", "full_tx": "Full transcript included",
           "translated_from": "Translated from {lang}", "actions": "ACTION ITEMS", "urgent": "URGENT",
           "sentiment": "SENTIMENT", "ribbon": "TRANSLATED",
           "cta": "Your meetings could arrive like this.", "pill": "Get Shoulder Surf free"},
    "es": {"report": "INFORME DE REUNIÓN", "transcript": "TRANSCRIPCIÓN", "plus": " + ", "readonly": "Enlace de solo lectura",
           "min": "{n} min", "people": "{n} personas", "person": "1 persona", "full_tx": "Transcripción completa incluida",
           "translated_from": "Traducido del {lang}", "actions": "ACCIONES", "urgent": "URGENTES",
           "sentiment": "TONO", "ribbon": "TRADUCIDO",
           "cta": "Tus reuniones podrían llegar así.", "pill": "Prueba Shoulder Surf gratis"},
    "fr": {"report": "COMPTE RENDU", "transcript": "TRANSCRIPTION", "plus": " + ", "readonly": "Lien en lecture seule",
           "min": "{n} min", "people": "{n} personnes", "person": "1 personne", "full_tx": "Transcription complète incluse",
           "translated_from": "Traduit de l'{lang}", "actions": "ACTIONS", "urgent": "URGENTES",
           "sentiment": "TON", "ribbon": "TRADUIT",
           "cta": "Vos réunions pourraient arriver ainsi.", "pill": "Essayer Shoulder Surf"},
}
LANG_NAMES = {"en": {"en": "English", "es": "Spanish", "fr": "French", "ja": "Japanese"},
              "es": {"en": "inglés", "es": "español", "fr": "francés", "ja": "japonés"},
              "fr": {"en": "anglais", "es": "espagnol", "fr": "français", "ja": "japonais"}}
MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
          "fr": ["janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"]}
DAYS = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "es": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
        "fr": ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]}


def _lang(tag: str | None) -> str:
    # Inter has no CJK glyphs, so ja cards render their chrome in English
    # until a CJK face is vendored; the NAME and summary are drawn as-is.
    l = (tag or "en").split("-")[0].lower()
    return l if l in STRINGS else "en"


_fonts: dict[tuple[int, int], ImageFont.FreeTypeFont] = {}


def font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    key = (size, weight)
    if key not in _fonts:
        f = ImageFont.truetype(str(FONT_PATH), size)
        try:
            f.set_variation_by_axes([max(14, min(32, size)), weight])
        except Exception:  # noqa: BLE001 — static fallback keeps rendering
            pass
        _fonts[key] = f
    return _fonts[key]


def _tracked(draw, xy, text, f, fill, tracking_em=0.0):
    """Letter-spaced text (Pillow has no tracking); returns end x."""
    x, y = xy
    extra = f.size * tracking_em
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += draw.textlength(ch, font=f) + extra
    return x


def _tracked_width(draw, text, f, tracking_em=0.0):
    return sum(draw.textlength(ch, font=f) + f.size * tracking_em for ch in text)


def _fit(draw, text, f, max_w, max_lines=1):
    """Greedy wrap into at most max_lines, ellipsis on overflow."""
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) > max_lines or (words and " ".join(lines).split() != words[:len(" ".join(lines).split())]):
        lines = lines[:max_lines]
    joined = " ".join(lines)
    if joined.split() != words:
        last = lines[-1] if lines else ""
        while last and draw.textlength(last + "…", font=f) > max_w:
            last = last[:-1].rstrip()
        lines[-1:] = [last + "…"]
    return lines


def _rounded_logo(size=42, radius=10):
    if not LOGO_PATH.exists():
        return None
    img = Image.open(LOGO_PATH).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def render_card(facts: dict) -> bytes:
    """facts: title, date (ISO or None), duration_seconds, attendees (int),
    action_count, urgent_count, sentiment (str|None), summary_line,
    transcript_included (bool), has_report (bool), share_language,
    source_language, cta_text?, pill_text?"""
    lang = _lang(facts.get("share_language"))
    S = STRINGS[lang]
    translated = bool(facts.get("share_language"))
    left = 190 if translated else 68

    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # --- header band
    d.rectangle((0, 0, W, 86), fill=INK)
    x = left
    logo = _rounded_logo()
    if logo is not None:
        img.paste(logo, (x, 22), logo); x += 42 + 14
    f_brand = font(22, 700)
    d.text((x, 30), "Shoulder Surf", font=f_brand, fill="white")
    x += d.textlength("Shoulder Surf", font=f_brand) + 18
    d.line((x, 30, x, 56), fill=(255, 255, 255, 46), width=1); x += 18
    label = S["report"] if facts.get("has_report") else ""
    if facts.get("transcript_included"):
        label = (label + S["plus"] if label else "") + S["transcript"]
    label = label or S["transcript"]
    # Right-aligned "read-only" first, so the artifact label has a hard
    # right edge and cannot collide with it (the Spanish/French labels are
    # long). Drop the ' + TRANSCRIPT' half, then ellipsize, to fit.
    f_ro = font(20, 500)
    ro_w = d.textlength(S["readonly"], font=f_ro)
    d.text((W - 68 - ro_w, 32), S["readonly"], font=f_ro, fill=DIM)
    f_lab = font(21, 700)
    avail = (W - 68 - ro_w - 28) - x
    if _tracked_width(d, label, f_lab, 0.13) > avail and facts.get("has_report"):
        label = S["report"]  # too long with both: keep just the report label
    while label and _tracked_width(d, label, f_lab, 0.13) > avail:
        label = label[:-1]
    _tracked(d, (x, 32), label, f_lab, LABEL_BLUE, 0.13)

    # --- body
    y = 86 + 46
    f_name = font(56, 700)
    for line in _fit(d, facts.get("title") or "", f_name, W - left - 68, max_lines=2):
        d.text((left, y), line, font=f_name, fill=INK); y += 64
    y += 6
    meta = []
    dt = facts.get("date")
    if dt:
        try:
            p = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
            meta.append(f"{DAYS[lang][p.weekday()]}, {MONTHS[lang][p.month - 1]} {p.day}")
        except ValueError:
            pass
    if facts.get("duration_seconds"):
        meta.append(S["min"].format(n=max(1, round(float(facts["duration_seconds"]) / 60))))
    n_people = int(facts.get("attendees") or 0)
    if n_people == 1:
        meta.append(S["person"])
    elif n_people > 1:
        meta.append(S["people"].format(n=n_people))
    if translated and facts.get("source_language"):
        src = LANG_NAMES.get(lang, LANG_NAMES["en"]).get(str(facts["source_language"]).split("-")[0].lower(), facts["source_language"])
        meta.append(S["translated_from"].format(lang=src))
    elif facts.get("transcript_included"):
        meta.append(S["full_tx"])
    f_meta = font(23, 500)
    d.text((left, y), " · ".join(meta), font=f_meta, fill=MUTED); y += 40

    # --- stat row with hairlines
    d.line((left, y, W - 68, y), fill=HAIR, width=2); y += 26
    x = left
    f_big = font(52, 700); f_cap = font(20, 600)
    def stat(x, value_draw, caption):
        vx = value_draw(x)
        _tracked(d, (x, y + 60), caption, f_cap, CAPTION, 0.06)
        return max(vx, x + _tracked_width(d, caption, f_cap, 0.06)) + 52
    def v_actions(x):
        t = str(int(facts.get("action_count") or 0)); d.text((x, y), t, font=f_big, fill=INK)
        return x + d.textlength(t, font=f_big)
    def v_urgent(x):
        # bolt glyph
        bx, by = x, y + 10
        d.polygon([(bx + 16, by), (bx + 4, by + 24), (bx + 14, by + 24), (bx + 10, by + 42), (bx + 24, by + 16), (bx + 14, by + 16)], fill=BOLT)
        t = str(int(facts.get("urgent_count") or 0)); d.text((bx + 34, y), t, font=f_big, fill=URGENT)
        return bx + 34 + d.textlength(t, font=f_big)
    def v_sent(x):
        # wave glyph
        pts = [(x + i, y + 30 + 9 * math.sin(i / 4.5)) for i in range(0, 36, 2)]
        d.line(pts, fill=BLUE, width=4, joint="curve")
        t = (facts.get("sentiment") or "—"); f_s = font(34, 700)
        d.text((x + 46, y + 12), t, font=f_s, fill=INK)
        return x + 46 + d.textlength(t, font=f_s)
    x = stat(x, v_actions, S["actions"])
    x = stat(x, v_urgent, S["urgent"])
    x = stat(x, v_sent, S["sentiment"])
    y += 60 + 26 + 26
    d.line((left, y, W - 68, y), fill=HAIR, width=2); y += 22

    # --- insight line
    f_ins = font(26, 400)
    for line in _fit(d, facts.get("summary_line") or "", f_ins, min(940, W - left - 68), max_lines=2):
        d.text((left, y), line, font=f_ins, fill=INSIGHT); y += int(26 * 1.45)

    # --- footer band
    d.rectangle((0, H - 96, W, H), fill=FOOT_BG)
    d.line((0, H - 96, W, H - 96), fill=FOOT_LINE, width=1)
    f_cta = font(22, 500)
    d.text((68, H - 96 + 36), facts.get("cta_text") or S["cta"], font=f_cta, fill=MUTED)
    pill = facts.get("pill_text") or S["pill"]; f_pill = font(21, 650)
    ph = 44
    pw = d.textlength(pill, font=f_pill) + 48
    px = W - 68 - pw; py = H - 96 + (96 - ph) // 2
    d.rounded_rectangle((px, py, px + pw, py + ph), radius=ph // 2, fill=BLUE)
    ty = py + (ph - (f_pill.getbbox(pill)[3] - f_pill.getbbox(pill)[1])) // 2 - f_pill.getbbox(pill)[1]
    d.text((px + 24, ty), pill, font=f_pill, fill="white")

    # --- translated ribbon (top-left, over the header)
    if translated:
        rib = Image.new("RGBA", (360, 360), (0, 0, 0, 0))
        rd = ImageDraw.Draw(rib)
        rd.rectangle((0, 150, 360, 206), fill=BLUE)
        f_rib = font(20, 700)
        text = S["ribbon"]
        tw = _tracked_width(rd, text, f_rib, 0.16)
        _tracked(rd, ((360 - tw) / 2, 165), text, f_rib, "white", 0.16)
        rib = rib.rotate(45, resample=Image.BICUBIC, expand=False)
        img.paste(rib, (-110, -110), rib)

    out = io.BytesIO(); img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _manifest_share_language(manifest: dict) -> str | None:
    """SS writes the shared language as camelCase `shareLanguage`; accept
    the snake form too. Reading the wrong key rendered the card in English
    (no localization, no ribbon) on a translated share."""
    v = manifest.get("shareLanguage") or manifest.get("share_language")
    return v if isinstance(v, str) and v.strip() else None


def _insight_line(text: str) -> str:
    """One real sentence for the card, not a boilerplate heading or raw
    markdown. The summary the client sends often starts with a heading
    line ('# Resumen de la Reunion', '## Meeting Summary'); skip those and
    strip inline markdown, then take the first substantive sentence."""
    import re as _re
    BOILER = ("meeting summary", "resumen de la reunion", "resumen de la reunión",
              "résumé de la réunion", "会議の要約", "会議のまとめ")
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = _re.sub(r"^#{1,6}\s*", "", line)            # drop heading marks
        line = _re.sub(r"^[-*\u2022]\s+", "", line)        # drop a leading bullet
        line = _re.sub(r"[*_`#]", "", line)                 # strip inline markdown emphasis
        line = _re.sub(r"\s{2,}", " ", line).strip()
        if not line or line.lower().rstrip(":").strip() in BOILER:
            continue
        # If the line is itself a short label ('Topic: X'), keep the value.
        return line
    return ""


def facts_from_share(row, bundle: dict, *, audio_count: int = 0, cta_text: str | None = None,
                     pill_text: str | None = None) -> dict:
    """Card facts from the share row (headers SS sent, localized when the
    share is translated) and the bundle bytes (everything else)."""
    meetings = bundle.get("meetings") or []
    m = meetings[0] if meetings else {}
    rec = m.get("record") or {}; rep = m.get("report") or {}
    manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), dict) else {}
    actions = [a for a in (rep.get("actions") or []) if isinstance(a, dict)]
    urgent = sum(1 for a in actions if str(a.get("priority") or "").lower() in ("critical", "urgent", "high"))
    header = rep.get("header") or {}
    sent = (rep.get("sentiment") or {}).get("label") or rec.get("sentimentLabel")
    return {
        "title": row["title"], "date": row["meeting_date"], "duration_seconds": row["duration_seconds"],
        "attendees": len(header.get("attendees") or []),
        "action_count": len(actions), "urgent_count": urgent, "sentiment": sent,
        "summary_line": _insight_line(row["summary_line"] or header.get("summary") or rec.get("rollingSummary") or ""),
        "transcript_included": bool(row["transcript_included"]),
        "has_report": bool(rec.get("reportHTML") or rec.get("reportJSONData")),
        "share_language": _manifest_share_language(manifest),
        "source_language": rec.get("transcriptLanguage") if isinstance(rec.get("transcriptLanguage"), str) else None,
        "audio_count": audio_count, "cta_text": cta_text, "pill_text": pill_text,
    }
