"""The link-preview card for a shared meeting: STATUS-LED (Scott's Claude
Design "iMessage Card Redesign", 2026-08-26), replacing the 2b ledger.

iMessage already draws the meeting title and the domain in the caption
under the image, so the image spends every pixel on what iMessage cannot
show: open items, urgent items, one named item, sentiment. Rules R1-R9
from the design, applied here:
  R1 title is caption-only (og:title), never rasterised. Briefly flipped
     on 2026-08-26 at Scott's request so the subject took the top slot,
     and reverted on 08-28 when he saw it in a real thread: iMessage
     draws og:title in the caption directly under the image, so the
     subject was printed twice in one bubble. That is the exact
     redundancy this redesign was commissioned to remove, and it is only
     visible in a real conversation, which is why it survived a design
     review and two rounds of rendered samples;
  R2 nothing button-shaped inside the image, no CTA copy;
  R3 attribution (wordmark + artifact label) replaces the CTA;
  R4 counts are the headline, urgent in amber only when > 0, no glyph;
  R5 one real item, named and dated, 88 chars, then "+ n more";
  R6 sentiment footer-right, labelled, lowercase, <= 34 chars, or omitted;
  R7 zero state: "No open items" + the summary line + the artifact note;
  R8 translation is a top-right chip replacing the artifact label;
  R9 localise the whole card or none of it (the card's locale is the
     shared language, else the language the meeting was held in).

One deliberate deviation from the mock: R5's "Due Thu — Priya to ..."
uses an em dash; served copy carries no dashes (standing rule), so the
rendered line is "Due Thu: Priya to ...".

Two stages on purpose: plan_card() decides every string the image will
carry (caps applied, locale resolved) and is what the tests pin;
render_card() only draws the plan. 1200x630 PNG, Inter (OFL, vendored).
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("ghostpour.share_card")

W, H = 1200, 630
CARD_VERSION = 8  # bump on EVERY plan or render change; the sidecar name carries it
FONT_PATH = Path(__file__).resolve().parent.parent / "static" / "fonts" / "Inter.ttf"
LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "share" / "icon-512.png"

PLATE = "#0B0B0F"; WHITE = "#FFFFFF"; BODY = "#F2F2F7"; DIM = "#AEAEB2"; GREY = "#8E8E93"
# The ledger sandwich (Scott 2026-08-28): a light card closed top and
# bottom by black bars, so it reads as one pill in the bubble. The bars
# also make the card self-framing against a light OR a dark iMessage
# bubble, which is what the light/dark question was really after.
PAPER = "#FFFFFF"; INK = "#0B0F14"; LABEL_BLUE = "#6FB6FF"; MUTED = "#6B7480"
CAPTION = "#8A93A0"; HAIR = "#ECEDF0"; INSIGHT = "#3C3C43"; URGENT = "#C77700"; BOLT = "#FF9F0A"
BAR_TOP, BAR_BOT = 96, 96
CHIP = "#C7C7CC"; AMBER = "#FF9F0A"; BLUE = "#4DA3FF"
RULE = (40, 40, 44)       # 1px rgba(255,255,255,0.12) over the plate
CHIP_LINE = (61, 61, 66)  # rgba(255,255,255,0.22) over the plate
PAD_X, PAD_TOP, PAD_BOTTOM = 56, 44, 44

LINE1_CAP, LINE2_CAP, SUMMARY_CAP, SENTIMENT_CAP, SECOND_CAP = 88, 72, 96, 34, 40

STRINGS = {
    "en": {"report": "MEETING REPORT", "transcript": "TRANSCRIPT", "plus": " + ",
           "open_one": "1 open item", "open_n": "{n} open items", "open_none": "No open items",
           "urgent": "{n} urgent", "urgent_one": "1 urgent",
           "due_who": "Due {day}: {who} to {text}", "due": "Due {day}: {text}", "who": "{who} to {text}",
           "more": "+ {n} more, incl. {second}", "more_plain": "+ {n} more",
           "sentiment": "Sentiment: {s}",
           "min": "{n} min", "people": "{n} people", "person": "1 person",
           "stat_actions": "ACTION ITEMS", "stat_urgent": "URGENT", "stat_sentiment": "SENTIMENT",
           "readonly": "Read-only link",
           "full_tx": "Full transcript included", "report_note": "Meeting report included",
           "translated_from": "Translated from {lang}"},
    "es": {"report": "INFORME DE REUNIÓN", "transcript": "TRANSCRIPCIÓN", "plus": " + ",
           "open_one": "1 tarea abierta", "open_n": "{n} tareas abiertas", "open_none": "Sin tareas abiertas",
           "urgent": "{n} urgentes", "urgent_one": "1 urgente",
           "due_who": "Para el {day}: {who} debe {text}", "due": "Para el {day}: {text}", "who": "{who} debe {text}",
           "more": "+ {n} más, incl. {second}", "more_plain": "+ {n} más",
           "sentiment": "Tono: {s}",
           "min": "{n} min", "people": "{n} personas", "person": "1 persona",
           "stat_actions": "TAREAS", "stat_urgent": "URGENTES", "stat_sentiment": "TONO",
           "readonly": "Enlace de solo lectura",
           "full_tx": "Transcripción completa incluida", "report_note": "Informe de reunión incluido",
           "translated_from": "Traducido del {lang}"},
    "fr": {"report": "COMPTE RENDU", "transcript": "TRANSCRIPTION", "plus": " + ",
           "open_one": "1 action ouverte", "open_n": "{n} actions ouvertes", "open_none": "Aucune action ouverte",
           "urgent": "{n} urgentes", "urgent_one": "1 urgente",
           "due_who": "Pour {day} : {who} doit {text}", "due": "Pour {day} : {text}", "who": "{who} doit {text}",
           "more": "+ {n} de plus, dont {second}", "more_plain": "+ {n} de plus",
           "sentiment": "Ton : {s}",
           "min": "{n} min", "people": "{n} personnes", "person": "1 personne",
           "stat_actions": "ACTIONS", "stat_urgent": "URGENTES", "stat_sentiment": "TON",
           "readonly": "Lien en lecture seule",
           "full_tx": "Transcription complète incluse", "report_note": "Compte rendu inclus",
           "translated_from": "Traduit de l'{lang}"},
}
LANG_NAMES = {"en": {"en": "English", "es": "Spanish", "fr": "French", "ja": "Japanese"},
              "es": {"en": "inglés", "es": "español", "fr": "francés", "ja": "japonés"},
              "fr": {"en": "anglais", "es": "espagnol", "fr": "français", "ja": "japonais"}}
MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
          "fr": ["janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"]}
DAYS = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "es": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
        "fr": ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]}
# R9: the sentiment CATEGORY is a wire enum (English keys, never translated by
# the model), so it is the one thing that can be said in the card's locale
# no matter which language the report was generated in.
SENTIMENT_WORDS = {
    "en": {"positive": "upbeat", "collaborative": "collaborative", "informational": "informational",
           "cautious": "cautious", "pressured": "under pressure", "tense": "tense",
           "disconnected": "disconnected", "decisive": "decisive"},
    "es": {"positive": "positivo", "collaborative": "colaborativo", "informational": "informativo",
           "cautious": "cauteloso", "pressured": "bajo presión", "tense": "tenso",
           "disconnected": "desconectado", "decisive": "decidido"},
    "fr": {"positive": "positif", "collaborative": "collaboratif", "informational": "informatif",
           "cautious": "prudent", "pressured": "sous pression", "tense": "tendu",
           "disconnected": "distant", "decisive": "décisif"},
}
_WEEKDAYS = {  # free-text deadlines that name a day, any of our locales
    "monday": 0, "mon": 0, "lunes": 0, "lundi": 0, "tuesday": 1, "tue": 1, "martes": 1, "mardi": 1,
    "wednesday": 2, "wed": 2, "miércoles": 2, "miercoles": 2, "mercredi": 2, "thursday": 3, "thu": 3,
    "jueves": 3, "jeudi": 3, "friday": 4, "fri": 4, "viernes": 4, "vendredi": 4, "saturday": 5, "sat": 5,
    "sábado": 5, "sabado": 5, "samedi": 5, "sunday": 6, "sun": 6, "domingo": 6, "dimanche": 6,
}
_URGENT = ("critical", "urgent", "high")
_NOBODY = ("", "multiple", "unknown", "tbd", "unassigned", "n/a", "none")


def _lang(tag: str | None) -> str:
    # Inter has no CJK glyphs, so a ja card renders its chrome in English
    # until a CJK face is vendored; content strings are drawn as-is.
    l = (tag or "en").split("-")[0].lower()
    return l if l in STRINGS else "en"


def card_locale(share_language: str | None, source_language: str | None) -> str:
    """R9: the shared language when the share is translated, else the
    language the meeting was held in. One locale for the whole card."""
    return _lang(share_language or source_language)


def _cap(text: str, n: int) -> str:
    """Hard char cap on a word boundary, with an ellipsis; never mid-word."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= n:
        return text
    cut = text[: n - 1].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip(" ,;:")
    return cut + "…"


def _short_day(deadline, lang: str) -> str | None:
    """A short day label for "Due {day}" from a free-text or ISO deadline,
    or None when it cannot be said briefly. Returns (label) and stores the
    sort key on the side via _due_sort_key."""
    if not isinstance(deadline, str) or not deadline.strip():
        return None
    raw = deadline.strip()
    d = _parse_date(raw)
    if d is not None:
        return f"{DAYS[lang][d.weekday()]} {d.day} {MONTHS[lang][d.month - 1]}" if abs((d - date.today()).days) > 6 \
            else DAYS[lang][d.weekday()]
    wd = _WEEKDAYS.get(raw.lower().rstrip("."))
    if wd is not None:
        return DAYS[lang][wd]
    # Anything else is NOT a day and must not be prefixed with "Due".
    # Two failures, one rule (Scott 2026-08-28, from a real card): the
    # deadline "7 to 14 days" is a DURATION, and it rendered as "Para el
    # 7 to 14 days" ("on the 7 to 14 days"), which is nonsense in Spanish
    # and wrong in English too. And any free text here is by definition
    # in the report's original language, so on a translated card it would
    # print an English fragment inside Spanish chrome. A date or a
    # weekday we can localize; arbitrary prose we cannot, so it is
    # dropped and the task text (which usually carries the timing anyway)
    # stands on its own.
    return None


def _parse_date(raw: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%b %d, %Y", "%B %d, %Y", "%b %d", "%B %d", "%d %b %Y", "%d %B %Y"):
        try:
            d = datetime.strptime(raw, fmt)
            if "%Y" not in fmt:
                d = d.replace(year=date.today().year)
            return d.date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _due_sort_key(deadline) -> tuple:
    if not isinstance(deadline, str) or not deadline.strip():
        return (2, "")
    d = _parse_date(deadline.strip())
    if d is not None:
        return (0, d.isoformat())
    wd = _WEEKDAYS.get(deadline.strip().lower().rstrip("."))
    if wd is not None:
        return (0, f"wd{wd}")
    return (1, deadline.strip().lower())


def open_items(actions: list) -> list[dict]:
    """The report's action items, urgent first, then earliest due, then
    the report's own order. An item flagged done/closed is not open."""
    out = []
    for i, a in enumerate(actions or []):
        if not isinstance(a, dict):
            continue
        st = str(a.get("status") or "").lower()
        if a.get("done") is True or st in ("done", "closed", "completed"):
            continue
        text = a.get("task") or a.get("text") or a.get("title")
        if not isinstance(text, str) or not text.strip():
            continue
        out.append((0 if str(a.get("priority") or "").lower() in _URGENT else 1,
                    _due_sort_key(a.get("deadline") or a.get("due")), i, a))
    out.sort(key=lambda t: t[:3])
    return [a for *_, a in out]


def _who(a: dict) -> str | None:
    who = a.get("owner") or a.get("assignee")
    if not isinstance(who, str) or who.strip().lower() in _NOBODY:
        return None
    return who.strip()


def _text(a: dict) -> str:
    t = (a.get("task") or a.get("text") or a.get("title") or "").strip()
    return t[0].lower() + t[1:] if t and t[0].isupper() and not t[:2].isupper() else t


def item_line(a: dict, lang: str) -> str:
    """R5: "Due {day}: {who} to {text}", each part dropped when unknown."""
    S = STRINGS[lang]
    day = _short_day(a.get("deadline") or a.get("due"), lang)
    who = _who(a)
    text = _text(a)
    if day and who:
        line = S["due_who"].format(day=day, who=who, text=text)
    elif day:
        line = S["due"].format(day=day, text=text)
    elif who:
        line = S["who"].format(who=who, text=text)
    else:
        line = text[0].upper() + text[1:] if text else ""
    return _cap(line, LINE1_CAP)


def _sentiment(facts: dict, lang: str) -> str | None:
    """R6 + R9: the category word in the card's locale; the model's label
    only when it is already in that locale (untranslated share); else none."""
    cat = str(facts.get("sentiment_category") or "").lower().strip()
    word = SENTIMENT_WORDS.get(lang, SENTIMENT_WORDS["en"]).get(cat)
    if word:
        return word
    # Translated at render time when the category enum did not carry it
    # (see translate_card_facts): before this the whole segment was
    # dropped on a translated share rather than print English in Spanish
    # chrome, which is why Scott's card showed no sentiment line at all.
    trans = facts.get("sentiment_translated")
    if isinstance(trans, str) and trans.strip():
        return _cap(trans.strip().lower(), SENTIMENT_CAP)
    label = facts.get("sentiment")
    if isinstance(label, str) and label.strip() and not facts.get("share_language"):
        return _cap(label.strip().lower(), SENTIMENT_CAP)
    return None


def plan_card(facts: dict) -> dict:
    """Every string the image will carry, decided once. facts: title,
    date, duration_seconds, attendees (int), actions (list), sentiment
    (label), sentiment_category, summary_line, transcript_included,
    has_report, share_language, source_language."""
    lang = card_locale(facts.get("share_language"), facts.get("source_language"))
    S = STRINGS[lang]
    items = open_items(facts.get("actions") or [])
    n = len(items)
    urgent = sum(1 for a in items if str(a.get("priority") or "").lower() in _URGENT)
    translated = bool(facts.get("share_language")) and \
        _lang(facts.get("share_language")) != _lang(facts.get("source_language")) if facts.get("source_language") \
        else bool(facts.get("share_language"))

    label = S["report"] if facts.get("has_report") else ""
    if facts.get("transcript_included"):
        label = (label + S["plus"] if label else "") + S["transcript"]
    label = label or S["transcript"]
    chip = None
    if translated:
        src = LANG_NAMES.get(lang, LANG_NAMES["en"]).get(
            str(facts.get("source_language") or "").split("-")[0].lower(), facts.get("source_language") or "")
        chip = S["translated_from"].format(lang=src) if src else None

    if n == 0:
        headline, urgent_text = S["open_none"], None
        line1 = _cap(facts.get("summary_line") or "", SUMMARY_CAP)
        line2 = S["full_tx"] if facts.get("transcript_included") else (S["report_note"] if facts.get("has_report") else "")
    else:
        headline = S["open_one"] if n == 1 else S["open_n"].format(n=n)
        urgent_text = (S["urgent_one"] if urgent == 1 else S["urgent"].format(n=urgent)) if urgent else None
        line1 = item_line(items[0], lang)
        rest = n - 1
        if rest and len(items) > 1:
            line2 = _cap(S["more"].format(n=rest, second=_cap(_text(items[1]), SECOND_CAP)), LINE2_CAP)
        elif rest:
            line2 = S["more_plain"].format(n=rest)
        else:
            line2 = ""

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
    people = int(facts.get("attendees") or 0)
    if people == 1:
        meta.append(S["person"])
    elif people > 1:
        meta.append(S["people"].format(n=people))
    sent = _sentiment(facts, lang)
    note = S["full_tx"] if facts.get("transcript_included") else (S["report_note"] if facts.get("has_report") else "")
    return {
        # The ledger's own fields: the stat row wants the bare sentiment
        # value (its caption says SENTIMENT), and the meta line carries
        # what the share holds.
        "sentiment_value": sent,
        "contents_note": note,
        "stat_labels": (S["stat_actions"], S["stat_urgent"], S["stat_sentiment"]),
        "readonly": S["readonly"],
        "lang": lang, "label": label, "chip": chip,
        "headline": headline, "urgent": urgent_text,
        "line1": line1, "line2": line2,
        "footer_left": " · ".join(meta),
        "footer_right": S["sentiment"].format(s=sent) if sent else None,
        "open_count": n, "urgent_count": urgent,
    }


def headline_text(plan: dict) -> str:
    """The headline as one string, for og:description on clients that
    show it (Slack, WhatsApp); iMessage ignores it."""
    return plan["headline"] + (f", {plan['urgent']}" if plan.get("urgent") else "")


# --- drawing ------------------------------------------------------------------

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
    x, y = xy
    extra = f.size * tracking_em
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += draw.textlength(ch, font=f) + extra
    return x


def _tracked_width(draw, text, f, tracking_em=0.0):
    return sum(draw.textlength(ch, font=f) + f.size * tracking_em for ch in text)


def _wrap(draw, text, f, max_w, max_lines):
    """Greedy wrap to at most max_lines; the plan already capped chars, this
    only breaks lines and ellipsizes if the pixels still overflow."""
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
    if " ".join(lines).split() != words:
        last = lines[-1] if lines else ""
        while last and draw.textlength(last + "…", font=f) > max_w:
            last = last[:-1].rstrip()
        lines[-1:] = [last + "…"]
    return lines


def _fit_one(draw, text, f, max_w):
    return _wrap(draw, text, f, max_w, 1)[0] if text else ""


def _rounded_logo(size=50, radius=13):
    if not LOGO_PATH.exists():
        return None
    img = Image.open(LOGO_PATH).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def render_card(facts: dict) -> bytes:
    """The ledger sandwich (Scott 2026-08-28): a light card closed by a
    black bar top and bottom so the bubble reads as one pill.

    The subject is deliberately NOT drawn: iMessage prints og:title in the
    caption immediately below the image, and printing it here too is the
    duplication this card exists to avoid. The stat row is the hero,
    because open items, urgency and sentiment are exactly what the
    caption cannot show."""
    import math

    plan = plan_card(facts)
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    right = W - PAD_X
    body_bottom = H - BAR_BOT

    # --- top bar: brand, artifact label, and the read-only note or chip ---
    d.rectangle((0, 0, W, BAR_TOP), fill=PLATE)
    x = PAD_X
    logo = _rounded_logo(size=44, radius=11)
    if logo is not None:
        img.paste(logo, (x, (BAR_TOP - 44) // 2), logo)
        x += 44 + 14
    f_brand = font(28, 700)
    by = (BAR_TOP - 34) // 2
    d.text((x, by), "Shoulder Surf", font=f_brand, fill="white")
    x += d.textlength("Shoulder Surf", font=f_brand) + 18
    d.line((x, by + 4, x, by + 28), fill=(70, 74, 82), width=2)
    x += 18

    # right edge first, so the artifact label can never collide with it
    f_right = font(21, 500)
    if plan["chip"]:
        cw = d.textlength(plan["chip"], font=f_right) + 40
        ch = 40
        cx, cy = right - cw, (BAR_TOP - ch) // 2
        d.rounded_rectangle((cx, cy, cx + cw, cy + ch), radius=ch // 2, outline=CHIP_LINE, width=2)
        d.text((cx + 20, cy + 9), plan["chip"], font=f_right, fill=CHIP)
        right_edge = cx
    else:
        rw = d.textlength(plan["readonly"], font=f_right)
        d.text((right - rw, by + 7), plan["readonly"], font=f_right, fill=GREY)
        right_edge = right - rw

    f_lab = font(21, 700)
    label = plan["label"]
    avail = right_edge - x - 28
    if _tracked_width(d, label, f_lab, 0.11) > avail and facts.get("has_report"):
        label = STRINGS[plan["lang"]]["report"]
    while label and _tracked_width(d, label, f_lab, 0.11) > avail:
        label = label[:-1]
    _tracked(d, (x, by + 6), label, f_lab, LABEL_BLUE, 0.11)

    # --- meta: when, how long, who, what the share holds ---
    y = BAR_TOP + 52
    meta = " · ".join(p for p in (plan["footer_left"], plan["contents_note"]) if p)
    f_meta = font(24, 500)
    d.text((PAD_X, y), _fit_one(d, meta, f_meta, right - PAD_X), font=f_meta, fill=MUTED)

    # --- the stat row, which is the whole point of the image ---
    y += 56
    d.line((PAD_X, y, right, y), fill=HAIR, width=2)
    y += 34
    f_big = font(56, 700)
    f_cap = font(19, 600)
    cap_y = y + 70
    lab_actions, lab_urgent, lab_sentiment = plan["stat_labels"]
    sx = PAD_X

    def _stat(sx, draw_value, caption) -> int:
        end_x = draw_value(sx)
        _tracked(d, (sx, cap_y), caption, f_cap, CAPTION, 0.06)
        return max(end_x, sx + _tracked_width(d, caption, f_cap, 0.06)) + 56

    if plan["open_count"] == 0:
        # "0 / 0" reads as a worthless meeting; say the state instead.
        def _v_zero(px):
            f_zero = font(40, 700)
            d.text((px, y + 12), plan["headline"], font=f_zero, fill=INK)
            return px + d.textlength(plan["headline"], font=f_zero)
        sx = _stat(sx, _v_zero, "")
    else:
        def _v_actions(px):
            t = str(plan["open_count"])
            d.text((px, y), t, font=f_big, fill=INK)
            return px + d.textlength(t, font=f_big)
        sx = _stat(sx, _v_actions, lab_actions)
        if plan["urgent_count"]:
            def _v_urgent(px):
                bx, byy = px, y + 12
                d.polygon([(bx + 17, byy), (bx + 4, byy + 26), (bx + 15, byy + 26),
                           (bx + 11, byy + 46), (bx + 26, byy + 17), (bx + 15, byy + 17)], fill=BOLT)
                t = str(plan["urgent_count"])
                d.text((bx + 36, y), t, font=f_big, fill=URGENT)
                return bx + 36 + d.textlength(t, font=f_big)
            sx = _stat(sx, _v_urgent, lab_urgent)

    if plan["sentiment_value"]:
        def _v_sent(px):
            pts = [(px + i, y + 34 + 10 * math.sin(i / 4.5)) for i in range(0, 38, 2)]
            d.line(pts, fill=BLUE, width=4, joint="curve")
            f_s = font(32, 700)
            t = _fit_one(d, plan["sentiment_value"], f_s, right - (px + 50))
            d.text((px + 50, y + 14), t, font=f_s, fill=INK)
            return px + 50 + d.textlength(t, font=f_s)
        _stat(sx, _v_sent, lab_sentiment)

    # --- the one named item, the reason to open the link ---
    y = cap_y + 46
    d.line((PAD_X, y, right, y), fill=HAIR, width=2)
    y += 34
    f_l1 = font(30, 600)
    for line in _wrap(d, plan["line1"], f_l1, right - PAD_X, 2):
        if y + 40 > body_bottom:
            break
        d.text((PAD_X, y), line, font=f_l1, fill=INSIGHT)
        y += 42
    # The meta line already names what the share holds, so the zero
    # state's artifact note would say it twice in one card.
    tail = "" if plan["line2"] == plan["contents_note"] else plan["line2"]
    if tail and y + 34 <= body_bottom:
        f_l2 = font(25, 400)
        d.text((PAD_X, y + 6), _fit_one(d, tail, f_l2, right - PAD_X), font=f_l2, fill=CAPTION)

    # --- bottom bar: closes the sandwich, deliberately empty ---
    d.rectangle((0, body_bottom, W, H), fill=PLATE)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def sidecar_path(storage_path: str) -> str:
    """Where the rendered card is cached beside the archive. The render
    version is in the name, so a renderer change never serves a stale
    card (the old sidecar was byte-identical after every deploy until
    someone deleted it as root). Purge removes `<archive>.card*.png`."""
    return f"{storage_path}.card.v{CARD_VERSION}.png"


# --- facts from the share -------------------------------------------------------

def _manifest_share_language(manifest: dict) -> str | None:
    """SS writes the shared language as camelCase `shareLanguage`; accept
    the snake form too."""
    v = manifest.get("shareLanguage") or manifest.get("share_language")
    return v if isinstance(v, str) and v.strip() else None


# The heading every summary opens with, in every language the app writes,
# with or without the article ("Résumé de Réunion" is what SS actually sends
# as X-Share-Summary-Line; the filter used to know only "Résumé de la
# réunion", so the live zero-state card said "Résumé de Réunion" instead of
# a sentence, 2026-08-26).
_BOILER = re.compile(
    r"^(?:meeting summary|summary|resumen(?: de (?:la )?reuni[oó]n)?|r[ée]sum[ée](?: de (?:la )?r[ée]union)?"
    r"|会議の(?:要約|まとめ)|要約)[:：]?$", re.I)


def _insight_line(text: str) -> str:
    """One real sentence, not a heading or raw markdown. EVERY markdown
    heading is skipped, not only the boilerplate one: the live French card
    (2026-08-26) took "## Origine de l'Entreprise", the summary's first
    section title, once "# Résumé de Réunion" was filtered. A heading is a
    label for what follows; the sentence is what follows."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or re.match(r"^#{1,6}\s", line) or re.match(r"^#{1,6}$", line):
            continue
        line = re.sub(r"^[-*•]\s+", "", line)
        line = re.sub(r"[*_`#]", "", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if not line or _BOILER.match(line):
            continue
        return line
    return ""


def summary_line_for_card(row, rec: dict, header: dict, share_language: str | None) -> str:
    """The first real sentence from the best source: the row's summary line
    (SS's X-Share-Summary-Line), then, on a translated share, the rendition
    summary in the shared language, then the report summary, then the
    rolling summary. Each candidate is cleaned; a candidate that is only a
    heading falls through instead of ending the search."""
    candidates = [row["summary_line"]]
    sl = (share_language or "").split("-")[0].lower()
    if sl:
        for r in (rec.get("transcriptRenditions") or []):
            if isinstance(r, dict) and str(r.get("lang") or "").split("-")[0].lower() == sl \
                    and isinstance(r.get("summary"), str):
                candidates.append(r["summary"])
    candidates += [header.get("summary"), rec.get("rollingSummary")]
    for c in candidates:
        line = _insight_line(c) if isinstance(c, str) else ""
        if line:
            return line
    return ""


def facts_from_share(row, bundle: dict, *, audio_count: int = 0, cta_text: str | None = None,
                     pill_text: str | None = None) -> dict:
    """Card facts from the share row and the bundle bytes. cta_text and
    pill_text are accepted for callers that still pass them and ignored:
    the image carries no CTA (R2)."""
    meetings = bundle.get("meetings") or []
    m = meetings[0] if meetings else {}
    rec = m.get("record") or {}
    rep = m.get("report") or {}
    manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), dict) else {}
    header = rep.get("header") or {}
    sent = rep.get("sentiment") or {}
    return {
        "title": row["title"], "date": row["meeting_date"], "duration_seconds": row["duration_seconds"],
        "attendees": len(header.get("attendees") or []),
        "actions": [a for a in (rep.get("actions") or []) if isinstance(a, dict)],
        "sentiment": sent.get("label") or rec.get("sentimentLabel"),
        "sentiment_category": sent.get("category"),
        "summary_line": summary_line_for_card(row, rec, header, _manifest_share_language(manifest)),
        "transcript_included": bool(row["transcript_included"]),
        "has_report": bool(rec.get("reportHTML") or rec.get("reportJSONData")),
        "share_language": _manifest_share_language(manifest),
        "source_language": rec.get("transcriptLanguage") if isinstance(rec.get("transcriptLanguage"), str) else None,
        "audio_count": audio_count,
    }


# --- render-time translation ----------------------------------------------------
#
# Scott, 2026-08-28, looking at a real Spanish card: "there are parts of
# this that were not translated, we should show it all in the translated
# language." He is right and it is structural. The CHROME localizes from
# our own tables, but the CONTENT the card quotes (the action item text)
# is read from the report's structured JSON, which exists only in the
# language the report was generated in. The bundle's translated rendition
# carries `summary`, `transcript` and `report_html` and NO structured
# report, so there is nothing translated for the card to read.
#
# Rather than wait on a new field from the client, translate the two or
# three strings the card actually shows, at render time, through the
# translation service we already own. The card is rendered once per share
# and cached as a sidecar, and translate_group is content-hash cached on
# top of that, so a re-mint costs nothing. Billed to the share OWNER,
# which is the same principle the page translations already use.
#
# Fail-open on purpose: a card that renders in the wrong language is a
# blemish, a card that fails to render is a broken link preview.

async def translate_card_facts(facts: dict, *, app_state, db, user, app_id: str | None = None,
                               request_id: str | None = None) -> dict:
    """`facts` with the card's quoted content in the card's own language."""
    from app.services import translations as tr

    target = card_locale(facts.get("share_language"), facts.get("source_language"))
    source = _lang(facts.get("source_language"))
    if not facts.get("share_language") or target == source or user is None:
        return facts

    items = open_items(facts.get("actions") or [])[:2]
    segments, marks = [], []
    for i, a in enumerate(items):
        text = (a.get("task") or a.get("text") or a.get("title") or "").strip()
        if text:
            segments.append({"id": f"a{i}", "text": text})
            marks.append((f"a{i}", a))
    # The sentiment label only needs translating when the category enum
    # could not supply the word; the enum path is free and exact.
    if not SENTIMENT_WORDS.get(target, {}).get(str(facts.get("sentiment_category") or "").lower().strip()):
        label = facts.get("sentiment")
        if isinstance(label, str) and label.strip():
            segments.append({"id": "s", "text": label.strip()})
    if not segments:
        return facts

    try:
        out, _cached = await tr.translate_group(
            app_state, db, user, segments, source, target, "summary",
            app_id=app_id, request_id=request_id)
    except Exception as e:  # noqa: BLE001 — never fail a card over its translation
        logger.warning("card_translation_skipped reason=%s", str(e)[:160])
        return facts

    by_id = {o["id"]: o["text"] for o in out if isinstance(o, dict)}
    swapped = {id(a): by_id[k] for k, a in marks if by_id.get(k)}
    new_facts = dict(facts)
    if swapped:
        new_facts["actions"] = [{**a, "task": swapped[id(a)]} if id(a) in swapped else a
                                for a in (facts.get("actions") or [])]
    if by_id.get("s"):
        new_facts["sentiment_translated"] = by_id["s"]
    return new_facts
