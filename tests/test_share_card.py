"""Status-led link-preview card (Scott's Claude Design "iMessage Card
Redesign", 2026-08-26), rendered server-side per share.

The plan is what the tests pin: every string the image carries, decided
by plan_card() under the design's rules R1-R9. Pixels are only checked
for size and format, and for the one visual property a ribbon used to
break (a translated card differs from a plain one).
"""
import io
import json
import sqlite3
import zipfile

import pytest
from pathlib import Path

from PIL import Image

from app.services.share_card import (CARD_VERSION, card_locale, facts_from_share, headline_text, item_line,
                                     open_items, plan_card, render_card, sidecar_path)

ORIGIN = "0C0C0C0C-1111-4222-8333-444444444444"
HDRS = {"Content-Type": "application/vnd.shouldersurf.archive", "X-Share-Title": "Q3 pricing kickoff",
        "X-Share-Date": "2026-08-24", "X-Share-Duration-Seconds": "2820",
        "X-Share-Summary-Line": "Converged on RAG over fine-tuning.", "X-Share-Transcript-Included": "true"}
ACTIONS = [{"task": "Close the telemetry gaps before CAB review", "owner": "Priya", "priority": "critical", "deadline": "Thursday"},
           {"task": "Production promotion sign-off", "owner": "Multiple", "priority": "high"},
           {"task": "Update the runbook", "owner": "Sam", "priority": "standard", "deadline": "2026-09-02"},
           {"task": "Schedule the retro", "owner": "Lee", "priority": "standard"}]


def _facts(**over):
    base = {"title": "Telemetry Data Quality Gaps, CAB Approval, and Production Promotion Planning",
            "date": "2026-08-26T15:00:00-05:00", "duration_seconds": 900, "attendees": 2, "actions": ACTIONS,
            "sentiment": "Steady and workmanlike", "sentiment_category": None,
            "summary_line": "Data Quality Gaps for Telemetry Data", "transcript_included": True, "has_report": True,
            "share_language": None, "source_language": "en"}
    return {**base, **over}


def _bundle(share_language=None, actions=None, spoken="en"):
    import base64
    report = {"header": {"title": "Q3 pricing kickoff", "attendees": ["A", "B", "C"], "summary": "s"},
              "actions": ACTIONS[:2] if actions is None else actions,
              "sentiment": {"category": "decisive", "label": "Decisive"}}
    rec = {"title": "Q3 pricing kickoff", "durationSeconds": 2820.0, "rollingSummary": "s", "transcript": "A hi",
           "transcriptLanguage": spoken, "reportJSONData": base64.b64encode(json.dumps(report).encode()).decode()}
    manifest = {"formatVersion": 1}
    if share_language:
        manifest["shareLanguage"] = share_language
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest)); z.writestr(f"meetings/{ORIGIN}.json", json.dumps(rec))
    return buf.getvalue()


def _share(client, user, body):
    r = client.post("/v1/shares", content=body, headers={**user["headers"], **HDRS})
    assert r.status_code == 200, r.text
    return r.json()["url"].rsplit("/", 1)[1]


# --- R1, R2, R3: what the image never carries and what replaces it -------------

def test_the_plan_never_carries_the_title_or_anything_button_shaped():
    p = plan_card(_facts())
    blob = json.dumps(p).lower()
    assert "telemetry data quality gaps, cab approval" not in blob        # R1: title is og:title only
    import re
    # R2 bans AFFORDANCES, not facts. "Read-only link" is a factual note
    # about what the recipient is getting and it lives in the top bar of
    # the ledger Scott chose on 2026-08-28; it is not something you can
    # tap and does not compete with the bubble's single tap target. The
    # CTA copy and the fake pill remain banned.
    for word in ("get shoulder surf", "your meetings could", "upgrade", "free"):
        assert word not in blob, word
    for verb in ("tap", "try", "get"):
        assert not re.search(rf"\b{verb}\b", blob), verb
    assert p["label"] == "MEETING REPORT + TRANSCRIPT"                     # R3: attribution
    assert "—" not in blob and "–" not in blob                            # no dashes in served copy


# --- R4, R5: counts headline and one named, dated item -------------------------

def test_counts_are_the_headline_and_urgent_rides_in_amber_only_when_present():
    p = plan_card(_facts())
    assert p["headline"] == "4 open items" and p["urgent"] == "2 urgent"
    assert p["open_count"] == 4 and p["urgent_count"] == 2
    p1 = plan_card(_facts(actions=[ACTIONS[2]]))
    assert p1["headline"] == "1 open item" and p1["urgent"] is None and p1["line2"] == ""
    one = plan_card(_facts(actions=[ACTIONS[0], ACTIONS[2]]))
    assert one["urgent"] == "1 urgent"                                       # singular, never "1 urgents"
    assert plan_card(_facts(actions=[ACTIONS[0]], share_language="es"))["urgent"] == "1 urgente"
    assert "⚡" not in json.dumps(p)


def test_the_top_item_is_urgent_first_then_earliest_due_and_reads_as_due_who_to_text():
    p = plan_card(_facts())
    assert p["line1"] == "Due Thu: Priya to close the telemetry gaps before CAB review"
    assert p["line2"] == "+ 3 more, incl. production promotion sign-off"
    ordered = open_items([ACTIONS[3], ACTIONS[2], ACTIONS[1], ACTIONS[0]])
    assert [a["task"] for a in ordered] == [ACTIONS[0]["task"], ACTIONS[1]["task"], ACTIONS[2]["task"], ACTIONS[3]["task"]]


def test_item_line_drops_unknown_parts_and_caps_at_88_on_a_word_boundary():
    assert item_line({"task": "Ship it", "owner": "Multiple"}, "en") == "Ship it"
    assert item_line({"task": "Ship it", "owner": "Ana", "deadline": "before the pilot starts, whenever that is"}, "en") == "Ana to ship it"
    assert item_line({"task": "Ship it", "deadline": "2026-12-25"}, "en").startswith("Due Fri 25 Dec: ship it")
    long = {"task": "word " * 40, "owner": "Ana", "deadline": "Friday"}
    line = item_line(long, "en")
    assert len(line) <= 88 and line.endswith("…") and not line.endswith(" …")


def test_done_items_are_not_open():
    p = plan_card(_facts(actions=[{**ACTIONS[0], "status": "done"}, {**ACTIONS[3], "done": True}, ACTIONS[2]]))
    assert p["headline"] == "1 open item" and p["line1"].startswith("Due")


# --- R6, R9: sentiment in the card's locale or not at all ----------------------

def test_sentiment_is_footer_right_labelled_lowercase_and_localized_by_category():
    p = plan_card(_facts())
    assert p["footer_right"] == "Sentiment: steady and workmanlike"
    es = plan_card(_facts(share_language="es", source_language="en", sentiment_category="pressured"))
    assert es["footer_right"] == "Tono: bajo presión"
    # a translated share with only an English label: R9 says omit, never mix
    fr = plan_card(_facts(share_language="fr", source_language="en", sentiment_category=None))
    assert fr["footer_right"] is None
    assert plan_card(_facts(sentiment=None, sentiment_category=None))["footer_right"] is None
    long = plan_card(_facts(sentiment="an extraordinarily long sentiment description that runs on", sentiment_category=None))
    assert len(long["footer_right"]) <= len("Sentiment: ") + 34


def test_the_card_locale_is_the_shared_language_else_the_spoken_one():
    assert card_locale("fr", "es") == "fr" and card_locale(None, "es") == "es" and card_locale(None, None) == "en"
    assert card_locale(None, "ja") == "en"   # no CJK face yet: English chrome
    p = plan_card(_facts(share_language=None, source_language="es", actions=[ACTIONS[0]]))
    assert p["headline"] == "1 tarea abierta" and p["line1"].startswith("Para el jue: Priya debe ")
    assert p["footer_left"].startswith("mié, ago 26")


# --- R7, R8: zero state and the translation chip --------------------------------

def test_zero_state_promotes_the_summary_and_the_artifact_note():
    p = plan_card(_facts(actions=[], summary_line="Antonio relata cómo nació la empresa y las lecciones de escalar.",
                         share_language="fr", source_language="es", sentiment_category="informational"))
    assert p["headline"] == "Aucune action ouverte" and p["urgent"] is None
    assert p["line1"].startswith("Antonio relata") and p["line2"] == "Transcription complète incluse"
    assert p["chip"] == "Traduit de l'espagnol" and p["footer_right"] == "Ton : informatif"
    long = plan_card(_facts(actions=[], summary_line="x" * 50 + " " + "y" * 60))
    assert len(long["line1"]) <= 96


def test_translation_is_a_chip_that_replaces_the_artifact_label_and_only_when_translated():
    p = plan_card(_facts(share_language="es", source_language="en"))
    assert p["chip"] == "Traducido del inglés"
    same = plan_card(_facts(share_language="en", source_language="en"))
    assert same["chip"] is None and same["label"] == "MEETING REPORT + TRANSCRIPT"


# --- rendering and the route ---------------------------------------------------

def test_render_is_a_1200x630_png_in_every_served_language_and_the_floor_cases():
    for lang in (None, "es", "fr", "ja"):
        img = Image.open(io.BytesIO(render_card(_facts(share_language=lang))))
        assert img.size == (1200, 630) and img.format == "PNG"
    floor = _facts(title="A" * 400, date=None, duration_seconds=None, attendees=0, actions=[], sentiment=None,
                   sentiment_category=None, summary_line="", transcript_included=False, has_report=False)
    assert Image.open(io.BytesIO(render_card(floor))).size == (1200, 630)
    plain = render_card(_facts(actions=[]))
    translated = render_card(_facts(actions=[], share_language="es"))
    assert plain != translated


def test_facts_come_off_the_bundle_and_the_row(client, pro_user):
    from app.services.share_bundle import read_bundle
    b = read_bundle(_bundle("es"))
    row = {"title": "Q3 pricing kickoff", "meeting_date": "2026-08-24", "duration_seconds": 2820,
           "summary_line": "Converged on RAG over fine-tuning.", "transcript_included": 1}
    f = facts_from_share(row, b)
    assert len(f["actions"]) == 2 and f["sentiment_category"] == "decisive" and f["attendees"] == 3
    assert f["share_language"] == "es" and f["source_language"] == "en"
    p = plan_card(f)
    assert p["headline"] == "2 tareas abiertas" and p["urgent"] == "2 urgentes" and p["chip"] == "Traducido del inglés"
    assert p["footer_right"] == "Tono: decidido"


def test_card_route_renders_once_caches_by_version_and_dies_with_the_share(client, pro_user, tmp_db_path):
    token = _share(client, pro_user, _bundle("es"))
    r = client.get(f"/s/{token}/card.png")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    conn = sqlite3.connect(tmp_db_path); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id, storage_path FROM meeting_shares WHERE token = ?", (token,)).fetchone(); conn.close()
    side = Path(sidecar_path(row["storage_path"]))
    assert side.exists() and side.name.endswith(f".card.v{CARD_VERSION}.png") and CARD_VERSION >= 7
    first = side.read_bytes()
    assert client.get(f"/s/{token}/card.png").content == first          # served from the sidecar
    r = client.delete(f"/v1/shares/{row['id']}", headers=pro_user["headers"])
    assert r.status_code == 200
    assert client.get(f"/s/{token}/card.png").status_code == 410


def test_og_description_leads_with_the_headline_when_the_card_is_dynamic(client, pro_user):
    from app.main import app as _app
    rc = _app.state.remote_configs
    share_cfg = ((rc.get("client-config") or {}).get("share") or {})
    token = _share(client, pro_user, _bundle(None))
    page = client.get(f"/s/{token}").text
    if share_cfg.get("dynamic_card"):
        assert "og:description' content='2 open items, 2 urgent · Report · Transcript · Converged on RAG" in page
    p = plan_card(facts_from_share({"title": "t", "meeting_date": None, "duration_seconds": None, "summary_line": "",
                                    "transcript_included": 1}, __import__("app.services.share_bundle", fromlist=["read_bundle"]).read_bundle(_bundle(None))))
    assert headline_text(p) == "2 open items, 2 urgent"


def test_og_image_points_at_the_card_only_when_the_flag_is_on(client, pro_user):
    from app.main import app as _app
    rc = _app.state.remote_configs
    token = _share(client, pro_user, _bundle(None))
    page = client.get(f"/s/{token}").text
    share_cfg = ((rc.get("client-config") or {}).get("share") or {})
    if share_cfg.get("dynamic_card"):
        assert f"/s/{token}/card.png" in page
    else:
        assert share_cfg.get("og_image_url", "") in page


def test_insight_line_drops_boilerplate_headings_and_markdown():
    from app.services.share_card import _insight_line
    assert _insight_line("# Resumen de la Reunión\nCigna holds the launch.") == "Cigna holds the launch."
    assert _insight_line("## Meeting Summary\n**Topic:** Legal cases") == "Topic: Legal cases"
    assert _insight_line("# Resumen de la Reunión") == ""
    # the article-less headings SS actually sends (live zero-state card, 2026-08-26)
    for heading in ("# Résumé de Réunion", "# Resumen de Reunión", "## Summary", "# 会議の要約", "Résumé de réunion:"):
        assert _insight_line(heading) == "", heading
    # every heading is skipped, not only the boilerplate one (live card 2026-08-26)
    fr = "# Résumé de Réunion\n\n## Origine de l'Entreprise\n- Antonio et Abraham ont décidé de créer une entreprise."
    assert _insight_line(fr) == "Antonio et Abraham ont décidé de créer une entreprise."
    assert _insight_line("## Only headings\n### And more") == ""


def test_a_heading_only_summary_line_falls_through_to_the_rendition_then_the_report():
    from app.services.share_card import summary_line_for_card
    rec = {"rollingSummary": "# Resumen de Reunión\n- Antonio relata cómo nació la empresa.",
           "transcriptRenditions": [{"lang": "fr", "summary": "# Résumé de Réunion\n- Antonio raconte la naissance de l'entreprise."}]}
    header = {"summary": "Antonio and Abraham recounted how the company began."}
    row = {"summary_line": "# Résumé de Réunion"}
    assert summary_line_for_card(row, rec, header, "fr") == "Antonio raconte la naissance de l'entreprise."
    assert summary_line_for_card(row, rec, header, None) == "Antonio and Abraham recounted how the company began."
    assert summary_line_for_card({"summary_line": "Real first line."}, rec, header, "fr") == "Real first line."
    assert summary_line_for_card({"summary_line": None}, {}, {}, None) == ""


# --- a day label must be a DAY (Scott 2026-08-28, from a real Spanish card) ------

def test_a_duration_is_not_a_day_and_never_gets_a_due_prefix():
    """The card read 'Para el 7 to 14 days' — "on the 7 to 14 days". The
    deadline was a duration, and free text is also untranslatable, so it
    is dropped rather than prefixed."""
    a = {"task": "Contact Scott when biopsy results are available", "owner": "Multiple",
         "deadline": "7 to 14 days"}
    assert item_line(a, "en") == "Contact Scott when biopsy results are available"
    assert item_line(a, "es") == "Contact Scott when biopsy results are available"
    for junk in ("7 to 14 days", "ASAP", "end of week", "next sprint", "TBD", "2 weeks"):
        line = item_line({"task": "Ship it", "deadline": junk}, "es")
        assert line == "Ship it", (junk, line)
        assert "Para el" not in line


def test_real_days_and_dates_still_label_and_localize():
    assert item_line({"task": "Ship it", "deadline": "Thursday"}, "en") == "Due Thu: ship it"
    assert item_line({"task": "Ship it", "deadline": "jueves"}, "es") == "Para el jue: ship it"
    assert item_line({"task": "Ship it", "deadline": "2026-12-25"}, "en").startswith("Due Fri 25 Dec")


# --- render-time translation of the quoted content ------------------------------

def _tr_facts(**over):
    base = _facts(share_language="es", source_language="en", sentiment="Warm and candid",
                  sentiment_category=None,
                  actions=[{"task": "Contact Scott when biopsy results are available",
                            "owner": "Multiple", "priority": "critical", "deadline": "7 to 14 days"},
                           {"task": "Send Scott an electronic document", "owner": "Multiple"}])
    return {**base, **over}


class _FakeUser:
    id = "u"; effective_tier = "pro"; is_trial = False


def _patch_tr(mapping=None, raises=None):
    from unittest.mock import AsyncMock, patch
    from app.services import translations as tr

    async def fake(app_state, db, user, segments, source, target, artifact, app_id=None, request_id=None):
        if raises:
            raise raises
        return [{"id": s["id"], "text": (mapping or {}).get(s["id"], "ES:" + s["text"])} for s in segments], False
    return patch.object(tr, "translate_group", AsyncMock(side_effect=fake))


@pytest.mark.asyncio
async def test_the_quoted_action_text_comes_back_in_the_cards_language():
    from app.services.share_card import translate_card_facts
    with _patch_tr({"a0": "Contactar a Scott cuando estén los resultados", "a1": "Enviar un documento",
                    "s": "Cálido y franco"}):
        out = await translate_card_facts(_tr_facts(), app_state=None, db=None, user=_FakeUser())
    p = plan_card(out)
    assert p["line1"] == "Contactar a Scott cuando estén los resultados"     # no English, no "Para el"
    assert "incl. enviar un documento" in p["line2"]
    assert p["footer_right"] == "Tono: cálido y franco"                       # was omitted entirely before
    assert "biopsy" not in json.dumps(p) and "Send Scott" not in json.dumps(p)


@pytest.mark.asyncio
async def test_an_untranslated_share_is_left_completely_alone():
    from app.services.share_card import translate_card_facts
    from unittest.mock import AsyncMock, patch
    from app.services import translations as tr
    with patch.object(tr, "translate_group", AsyncMock()) as m:
        f = _tr_facts(share_language=None)
        assert await translate_card_facts(f, app_state=None, db=None, user=_FakeUser()) is f
        same = _tr_facts(share_language="en", source_language="en")
        assert await translate_card_facts(same, app_state=None, db=None, user=_FakeUser()) is same
        assert not m.called, "nothing to translate must cost nothing"


@pytest.mark.asyncio
async def test_the_enum_path_stays_free_and_only_the_label_is_ever_sent():
    """A category we can name is exact and costs nothing; only an
    unmappable free-text label is worth a translation segment."""
    from app.services.share_card import translate_card_facts
    sent = {}
    from unittest.mock import AsyncMock, patch
    from app.services import translations as tr

    async def fake(app_state, db, user, segments, source, target, artifact, app_id=None, request_id=None):
        sent["ids"] = [s["id"] for s in segments]
        return [{"id": s["id"], "text": "x"} for s in segments], False
    with patch.object(tr, "translate_group", AsyncMock(side_effect=fake)):
        await translate_card_facts(_tr_facts(sentiment_category="pressured"),
                                   app_state=None, db=None, user=_FakeUser())
    assert sent["ids"] == ["a0", "a1"], "the mappable category must not be sent for translation"


@pytest.mark.asyncio
async def test_a_translation_failure_still_produces_a_card():
    """A card in the wrong language is a blemish; a card that fails to
    render is a broken link preview."""
    from app.services.share_card import translate_card_facts
    with _patch_tr(raises=RuntimeError("allocation exhausted")):
        out = await translate_card_facts(_tr_facts(), app_state=None, db=None, user=_FakeUser())
    assert Image.open(io.BytesIO(render_card(out))).size == (1200, 630)
    assert plan_card(out)["line1"].startswith("Contact Scott")     # original text, still a card


@pytest.mark.asyncio
async def test_no_owner_no_translation_rather_than_a_crash():
    from app.services.share_card import translate_card_facts
    f = _tr_facts()
    assert await translate_card_facts(f, app_state=None, db=None, user=None) is f


# --- the ledger sandwich (Scott 2026-08-28) -------------------------------------

def _px(png_bytes):
    return Image.open(io.BytesIO(png_bytes)).convert("RGB")


def test_the_card_is_a_light_panel_closed_by_a_black_bar_top_and_bottom():
    """Scott's ask: 'a black bar at the bottom, just black, no text' so it
    'looks like a complete little pill or tab'. Checked in PIXELS, because
    that is the whole of the change and a plan assertion cannot see it."""
    from app.services.share_card import BAR_BOT, BAR_TOP
    im = _px(render_card(_facts()))
    w, h = im.size
    for y in (2, BAR_TOP - 6):                       # inside the top bar
        assert sum(im.getpixel((w // 2, y))) < 120, y
    for y in (h - BAR_BOT + 6, h - 3):               # inside the bottom bar
        assert sum(im.getpixel((w // 2, y))) < 120, y
    assert sum(im.getpixel((w // 2, h // 2))) > 600  # the body is light
    # and the bottom bar carries NO text: every row in it is uniformly dark
    for y in range(h - BAR_BOT + 8, h - 4, 6):
        row = [sum(im.getpixel((x, y))) for x in range(40, w - 40, 20)]
        assert max(row) < 130, f"something is drawn in the bottom bar at y={y}"


def test_the_subject_is_still_not_drawn_anywhere_in_the_image():
    """The one thing that must survive every redesign: iMessage prints
    og:title in the caption, so the image drawing it too is the exact
    duplication Scott has now objected to twice."""
    p = plan_card(_facts())
    assert "title" not in p


def test_the_stat_row_survives_localisation_and_the_zero_state_says_so():
    es = plan_card(_facts(share_language="es", source_language="en", sentiment_category="pressured"))
    assert es["stat_labels"] == ("TAREAS", "URGENTES", "TONO")
    assert es["sentiment_value"] == "bajo presión"        # bare value; the caption says TONO
    assert es["readonly"] == "Enlace de solo lectura"
    zero = plan_card(_facts(actions=[]))
    assert zero["headline"] == "No open items" and zero["open_count"] == 0


def test_the_zero_state_does_not_say_what_the_share_holds_twice():
    """The meta line names the contents; the body must not repeat it.

    Checked in PIXELS. The first version of this test asserted only that
    the image was 1200x630, which is true whether or not the duplicate is
    drawn — a test that could not fail, caught by sabotaging the drop and
    watching it stay green."""
    from app.services.share_card import BAR_BOT
    f = _facts(actions=[], summary_line="A short recap of the call.")
    p = plan_card(f)
    assert p["contents_note"] == "Full transcript included"
    assert p["line2"] == p["contents_note"], "the plan still offers it; the renderer must drop it"
    im = _px(render_card(f))
    w, h = im.size
    # the band between the one-line summary and the bottom bar must be bare
    dark = sum(1 for y in range(432, h - BAR_BOT - 4, 3)
               for x in range(56, w - 56, 4) if sum(im.getpixel((x, y))) < 480)
    assert dark == 0, f"{dark} dark pixels below the summary: the contents note is drawn twice"


def test_every_language_and_the_floor_case_still_render():
    for lang in (None, "es", "fr", "ja"):
        assert _px(render_card(_facts(share_language=lang))).size == (1200, 630)
    floor = _facts(title=None, date=None, duration_seconds=None, attendees=0, actions=[], sentiment=None,
                   sentiment_category=None, summary_line="", transcript_included=False, has_report=False)
    assert _px(render_card(floor)).size == (1200, 630)
