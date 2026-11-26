"""The workbook renderers write their own words in the user's language.

Shoulder Surf, 2026-11-26: on a Brazilian Portuguese account a generated
plan workbook came back with the tabs "Slip" and "Receipts". The model had
not written those. The renderer had, and it had no locale parameter at
all, so every tab name, header, legend line, status word and date it
writes itself was English for everybody, under a plan in Portuguese.

What these tests hold, and why each one can fail:

  * the strings table is complete, legal for Excel, and dash free;
  * a workbook rendered for each locale has that locale's tab names, no
    English chrome left in its fixed cells, and cross-sheet references
    that point at tabs which exist under their LOCALIZED names;
  * the status word is one word in the cell, the dropdown and the
    formulas, because a translated cell under an English formula is a
    sheet whose risk highlighting silently stops;
  * the request's locale actually reaches the renderer through the route.

The sabotage for each is recorded in the PR that added this file.
"""

from __future__ import annotations

import datetime
import html
import io
import re
import zipfile
from pathlib import Path

import openpyxl
import pytest

from app.services import doc_strings
from app.services.doc_strings import (
    LABELS,
    LOCALES,
    SHEET_KEYS,
    STATUS_KEYS,
    STRINGS,
    DocStrings,
    resolve_locale,
)
from app.services.doc_templates import render_gantt, render_gantt_detailed

ROOT = Path(__file__).resolve().parent.parent
NON_EN = [loc for loc in LOCALES if loc != "en"]
TODAY = datetime.date(2026, 7, 21)

# Every string the MODEL would have supplied carries "Zz", so the chrome
# checks can tell our words from the plan's.
PLAN = {
    "project": "Zz Kit",
    "meeting_date": "2026-07-20",
    "tasks": [
        {"id": 1, "name": "Zz Fase", "type": "phase", "parent_id": None,
         "owner": None, "status": "in_progress", "start": "2026-07-06",
         "end": "2026-08-03", "depends_on": []},
        {"id": 2, "name": "Zz Uno", "type": "task", "parent_id": 1,
         "owner": "Zz Maya", "status": "in_progress", "start": "2026-07-06",
         "end": "2026-07-24", "depends_on": [], "percent_complete": 70,
         "effort": None,
         "commitments": [
             {"date": "2026-07-10", "as_of": "2026-07-06",
              "quote": "Zz quote uno", "speaker": "Zz Maya", "reason": None},
             {"date": "2026-07-24", "as_of": "2026-07-13",
              "quote": "Zz quote dos", "speaker": "Zz Maya",
              "reason": "Zz motivo"}],
         "evidence": [{"field": "percent_complete", "quote": "Zz setenta",
                       "speaker": "Zz Maya"}]},
        {"id": 3, "name": "Zz Dos", "type": "task", "parent_id": 1,
         "owner": "Zz Jordan", "status": "blocked", "start": "2026-07-06",
         "end": "2026-07-22", "depends_on": [], "percent_complete": None,
         "effort": "3 Zz", "conditional": "Zz cond",
         "evidence": [{"field": "effort", "quote": "Zz tres",
                       "speaker": "Zz Jordan"},
                      {"field": "owner", "quote": "Zz dueno",
                       "speaker": "Zz Jordan"}]},
        {"id": 4, "name": "Zz Tres", "type": "task", "parent_id": 1,
         "owner": "Zz Jordan", "status": "complete", "start": "2026-07-13",
         "end": "2026-07-17", "depends_on": []},
        {"id": 5, "name": "Zz Cuatro", "type": "task", "parent_id": 1,
         "owner": None, "status": "not_started", "start": "2026-07-27",
         "end": "2026-07-31", "depends_on": [4]},
        {"id": 6, "name": "Zz Cinco", "type": "task", "parent_id": 1,
         "owner": None, "status": "on_hold", "start": "2026-07-27",
         "end": "2026-07-29", "depends_on": []},
        {"id": 7, "name": "Zz Hito", "type": "milestone", "parent_id": 1,
         "owner": None, "status": "not_started", "start": "2026-08-03",
         "end": "2026-08-03", "depends_on": [5]},
        # undated: exercises the date coverage lines on two sheets
        {"id": 8, "name": "Zz Nada", "type": "task", "parent_id": 1,
         "owner": None, "status": "not_started", "start": None, "end": None,
         "depends_on": []},
    ],
}
HISTORY = [
    {"as_of": "2026-06-22", "created_at": "2026-06-22T18:00:00+00:00",
     "tasks": [{"id": 9, "name": "zz uno", "type": "task", "parent_id": 1,
                "owner": "Zz Maya", "status": "in_progress",
                "start": "2026-07-06", "end": "2026-07-08",
                "depends_on": []}]},
]

_SHEET_REF = re.compile(r"'((?:[^']|'')+)'!")
_BARE_REF = re.compile(r"(?<![\w'.])([A-Za-z_][\w.]*)!\$?[A-Z]")
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _blob(locale, *, history=HISTORY):
    return render_gantt_detailed(PLAN, today=TODAY, history=history,
                                 locale=locale)


def _wb(locale, **kw):
    return openpyxl.load_workbook(io.BytesIO(_blob(locale, **kw)))


def _expected_tabs(locale):
    s = STRINGS[locale]
    # creation order in render_gantt_detailed
    return [s["sheet_gantt"], s["sheet_slip"], s["sheet_receipts"],
            s["sheet_progress"]]


def _texts(wb):
    """Every piece of text a reader can see that is not a tab name: cell
    values, cell comments, conditional formatting formulas and dropdown
    lists. Yields (where, text)."""
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str):
                    yield f"{ws.title}!{c.coordinate}", c.value
                if c.comment is not None:
                    yield f"{ws.title}!{c.coordinate} comment", c.comment.text
        for rng in ws.conditional_formatting:
            for rule in rng.rules:
                for f in (rule.formula or []):
                    yield f"{ws.title} conditional format", f
        for dv in ws.data_validations.dataValidation:
            yield f"{ws.title} dropdown", dv.formula1 or ""


# ---------------------------------------------------------------- the table

def test_every_locale_carries_every_key():
    en = set(STRINGS["en"])
    assert len(en) > 90
    for loc in NON_EN:
        assert set(STRINGS[loc]) == en, (
            loc, sorted(en ^ set(STRINGS[loc])))


def test_placeholders_match_english_in_every_locale():
    for key, text in STRINGS["en"].items():
        want = sorted(_PLACEHOLDER.findall(text))
        for loc in NON_EN:
            got = sorted(_PLACEHOLDER.findall(STRINGS[loc][key]))
            assert got == want, (loc, key, got, want)


def test_every_contract_header_and_title_is_translated():
    """The contract lane's headers are ours too. A column added to a
    contract without a translation would render English between its
    translated neighbours, so the gap fails here instead."""
    from app.services.artifact_types import CONTRACTS
    declared = {c.label for c in CONTRACTS.values()}
    declared |= {col.label for c in CONTRACTS.values() for col in c.columns}
    assert len(declared) > 50
    for loc in NON_EN:
        missing = sorted(declared - set(LABELS[loc]))
        assert not missing, (loc, missing)
        blank = sorted(k for k, v in LABELS[loc].items() if not v.strip())
        assert not blank, (loc, blank)


_DASHES = {"—": "em dash", "–": "en dash", "―": "horizontal bar",
           "‒": "figure dash", "−": "minus sign", "〜": "wave dash",
           "～": "fullwidth tilde"}


def _all_strings():
    for loc in LOCALES:
        for key, text in STRINGS[loc].items():
            yield loc, key, text
        for key, text in LABELS[loc].items():
            yield loc, key, text


def test_no_string_uses_a_dash_as_punctuation():
    """The config dash scans read config/remote. These strings live in
    Python, where no existing scan reaches, so this is their scan."""
    seen = 0
    for loc, key, text in _all_strings():
        seen += 1
        for ch, name in _DASHES.items():
            assert ch not in text, f"{loc}.{key} carries a {name}"
        assert not re.search(r"\s-+\s|--|^-|-$", text), (
            f"{loc}.{key} uses a hyphen as punctuation: {text!r}")
        # a hyphen is fine only between two letters or digits
        for m in re.finditer("-", text):
            before, after = text[m.start() - 1], text[m.start() + 1]
            assert before.isalnum() and after.isalnum(), (loc, key, text)
    assert seen > 700


# ------------------------------------------------------ Excel's own rules

@pytest.mark.parametrize("locale", LOCALES)
def test_sheet_names_are_legal_in_excel(locale):
    names = DocStrings(locale).sheet_names()
    assert set(names) == set(SHEET_KEYS)
    others = {k: STRINGS[locale][k]
              for k in ("wb_readme_tab", "wb_sheet_default")}
    for key, name in {**names, **others}.items():
        assert name and name == name.strip(), (locale, key, name)
        assert len(name) <= 31, (locale, key, name, len(name))
        bad = set(name) & set("\\/?*[]:")
        assert not bad, (locale, key, name, bad)
        # legal in Excel only when doubled, and every cross-sheet formula
        # here quotes the name as '<name>'!
        assert "'" not in name, (locale, key, name)
    # unique within the workbook; Excel compares names without case
    folded = [n.casefold() for n in names.values()]
    assert len(set(folded)) == len(folded) == 4, (locale, names)


@pytest.mark.parametrize("locale", LOCALES)
def test_status_words_survive_the_dropdown_and_the_formulas(locale):
    words = DocStrings(locale).status_labels()
    assert list(words) == list(STATUS_KEYS)
    for key, w in words.items():
        assert w and w == w.strip(), (locale, key, w)
        assert "," not in w and '"' not in w, (locale, key, w)
    assert len({w.casefold() for w in words.values()}) == 5, (locale, words)
    # an inline data validation list is capped at 255 characters
    assert len(",".join(words.values())) <= 255


# ------------------------------------------------- one workbook per locale

@pytest.mark.parametrize("locale", LOCALES)
def test_tab_names_are_the_locale_s(locale):
    wb = _wb(locale)
    assert wb.sheetnames == _expected_tabs(locale)
    simple = openpyxl.load_workbook(io.BytesIO(
        render_gantt(PLAN, today=TODAY, locale=locale)))
    assert simple.sheetnames == [STRINGS[locale]["sheet_gantt"]]


def test_the_default_is_still_the_english_workbook():
    """No locale, and a locale we do not carry, both mean English, byte
    for byte the file an English account has always received."""
    plain = render_gantt_detailed(PLAN, today=TODAY, history=HISTORY)
    assert _blob("en") == plain
    assert _blob("de-DE") == plain
    assert openpyxl.load_workbook(io.BytesIO(plain)).sheetnames == [
        "Gantt View", "Slip", "Receipts", "Progress"]


def _is_plan_content(text: str) -> bool:
    return "Zz" in text or "zz" in text


@pytest.mark.parametrize("locale", NON_EN)
def test_no_english_chrome_is_left_in_a_translated_workbook(locale):
    """Two instruments, because either alone has a blind side.

    First, the table: no English string whose translation differs may
    appear in the translated workbook. Second, the two workbooks side by
    side: a cell that reads the same in English and in the translation
    has to be something that SHOULD read the same (the plan's own words,
    a date, a formula with no words in it), which catches a literal the
    renderer still hard-codes and the table never heard of."""
    wb = _wb(locale)
    texts = list(_texts(wb))
    assert len(texts) > 300
    en, loc = STRINGS["en"], STRINGS[locale]

    # 1. from the table
    checked = 0
    for key, en_text in en.items():
        if key.startswith("wb_") or loc[key] == en_text:
            continue
        for frag in _PLACEHOLDER.split(en_text):
            frag = frag.strip()
            if len(frag) < 4:
                continue
            checked += 1
            pat = re.compile(r"(?<!\w)" + re.escape(frag) + r"(?!\w)")
            hits = [(where, t[:80]) for where, t in texts
                    if not _is_plan_content(t) and pat.search(t)]
            assert not hits, (locale, key, frag, hits[:3])
    assert checked > 80

    # 2. side by side with the English workbook
    en_wb = _wb("en")
    same_on_purpose = {loc[k] for k in en if loc[k] == en[k]}
    leftovers = []
    compared = 0
    for ws_en, ws_loc in zip(en_wb.worksheets, wb.worksheets):
        for row in ws_en.iter_rows():
            for c in row:
                v = c.value
                if not isinstance(v, str):
                    continue
                other = ws_loc[c.coordinate].value
                compared += 1
                if other != v:
                    continue
                if v.startswith("="):
                    # a formula may only repeat when it quotes no words
                    words = [w for w in re.findall(r'"([^"]*)"', v)
                             if any(ch.isalpha() for ch in w)]
                    refs = _SHEET_REF.findall(v)
                    if not words and not refs:
                        continue
                elif (_is_plan_content(v)
                      or v.strip() in same_on_purpose
                      or not any(ch.isalpha() for ch in v)
                      or re.fullmatch(r"R\d+", v)              # receipt ref
                      or re.fullmatch(r"\d+(FS|SS|FF)(, \d+(FS|SS|FF))*", v)
                      or re.fullmatch(r"[A-Z]{1,2}", v)):      # initials
                    continue
                leftovers.append((ws_loc.title, c.coordinate, v[:60]))
    assert compared > 300
    assert not leftovers, (locale, leftovers[:8])


@pytest.mark.parametrize("locale", LOCALES)
def test_cross_sheet_references_resolve(locale):
    """Everything that names a sheet names one that exists: the Progress
    formulas, the Receipts hyperlinks, the print areas and titles, and
    the chart's series. Read from the saved file, since the chart and the
    defined names exist only there."""
    blob = _blob(locale)
    wb = openpyxl.load_workbook(io.BytesIO(blob))
    tabs = set(wb.sheetnames)
    gantt = STRINGS[locale]["sheet_gantt"]
    progress = STRINGS[locale]["sheet_progress"]

    # formulas in cells
    quoted = bare = 0
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                if not (isinstance(c.value, str) and c.value.startswith("=")):
                    continue
                for name in _SHEET_REF.findall(c.value):
                    quoted += 1
                    assert name.replace("''", "'") in tabs, (
                        ws.title, c.coordinate, name)
                    assert name == gantt
                bare += len(_BARE_REF.findall(c.value))
    assert quoted > 20      # the Progress sheet reads the Gantt dates
    assert bare == 0        # nothing references a sheet unquoted

    # hyperlinks: Receipts rows jump to the task's row on the Gantt
    links = 0
    for c in (c for row in wb[STRINGS[locale]["sheet_receipts"]].iter_rows()
              for c in row):
        if c.hyperlink is None:
            continue
        links += 1
        m = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+\d+)",
                         c.hyperlink.location or "")
        assert m, c.hyperlink.location
        assert m.group(1) in tabs and m.group(1) == gantt
        assert str(wb[m.group(1)][m.group(2)].value).strip().endswith(
            str(c.value))
    assert links >= 5

    # the saved package: defined names and the chart
    z = zipfile.ZipFile(io.BytesIO(blob))
    workbook_xml = html.unescape(z.read("xl/workbook.xml").decode("utf-8"))
    defined = re.findall(r"<definedName[^>]*>([^<]+)</definedName>",
                         workbook_xml)
    assert len(defined) >= 3          # two print areas and the print titles
    for d in defined:
        names = _SHEET_REF.findall(d)
        assert names, d
        assert set(names) <= tabs, (d, tabs)
    charts = [n for n in z.namelist() if n.startswith("xl/charts/chart")]
    assert len(charts) == 1
    chart_xml = html.unescape(z.read(charts[0]).decode("utf-8"))
    series_refs = re.findall(r"<(?:c:)?f>([^<]+)</(?:c:)?f>", chart_xml)
    assert len(series_refs) >= 12     # six series: a title and values each
    for ref in series_refs:
        names = _SHEET_REF.findall(ref)
        assert names == [progress], (ref, progress)
    # the ignored-errors mark is keyed by the Gantt tab's title
    assert b"<ignoredErrors>" in z.read("xl/worksheets/sheet1.xml")


@pytest.mark.parametrize("locale", LOCALES)
def test_one_status_word_in_the_cell_the_dropdown_and_the_formulas(locale):
    """The Status cell is compared by formula: the at-risk colouring, the
    live status dots and the phase percent rollup all test `$D="<word>"`.
    Translate the cell and leave the formula English and the sheet still
    opens, looks right, and has quietly stopped flagging anything."""
    words = DocStrings(locale).status_labels()
    ws = _wb(locale)[STRINGS[locale]["sheet_gantt"]]

    dropdowns = [dv.formula1 for dv in ws.data_validations.dataValidation]
    assert dropdowns == ['"' + ",".join(words.values()) + '"']

    cells = {ws.cell(r, 4).value for r in range(1, ws.max_row + 1)}
    cells -= {None, STRINGS[locale]["col_status"]}
    assert cells == set(words.values())     # the plan uses all five

    cf = [f for rng in ws.conditional_formatting for rule in rng.rules
          for f in (rule.formula or [])]
    cell_formulas = [c.value for row in ws.iter_rows() for c in row
                     if isinstance(c.value, str) and c.value.startswith("=")]
    compared = set()
    for f in cf + cell_formulas:
        for m in re.finditer(r'\$D\$?\d+(?::\$D\$?\d+)?(?:=|<>)"([^"]+)"', f):
            compared.add(m.group(1))
    # at risk: blocked, not started, complete. dots: all five. rollup:
    # complete. So every word is compared somewhere and nothing else is.
    assert compared == set(words.values()), (locale, compared)


def test_dates_follow_the_locale():
    """strftime("%b") is English whatever the locale, which is how a
    Portuguese strip read "Jul 21". Month and weekday names come from
    share_card's tables, the same ones the share card uses."""
    from app.services.share_card import DAYS, MONTHS
    d = datetime.date(2026, 7, 6)          # a Monday
    assert DocStrings("en").short_date(d) == d.strftime("%b %d") == "Jul 06"
    assert DocStrings("en").short_date(d, pad=False) == "Jul 6"
    assert DocStrings("pt").short_date(d) == "6 jul"
    assert DocStrings("es").short_date(d) == "6 jul"
    assert DocStrings("fr").short_date(d) == "6 juil"
    assert DocStrings("ja").short_date(d) == "7月6日"
    for loc in ("en", "es", "fr", "pt"):
        assert MONTHS[loc][6] in DocStrings(loc).short_date(d)
        assert [DocStrings(loc).day_letter(i) for i in range(7)] == [
            DAYS[loc][i][0].upper() for i in range(7)]
    assert "".join(DocStrings("en").day_letter(i) for i in range(7)) == "MTWTFSS"
    assert DocStrings("ja").day_letter(0) == "月"

    gv = _wb("pt")[STRINGS["pt"]["sheet_gantt"]]
    strip = gv.cell(2, 2).value
    assert "em risco em 21 jul" in strip and "Jul 21" not in strip
    assert "próximo marco: Zz Hito 3 ago" in strip
    row2 = [c.value for c in gv[2] if isinstance(c.value, str)][1:]
    assert row2[0] == "Semana de 6 jul"
    row3 = "".join(str(c.value) for c in gv[3][12:19])
    assert row3 == "STQQSSD"               # seg ter qua qui sex sáb dom
    weeks = [c.value for c in _wb("pt")[STRINGS["pt"]["sheet_progress"]]["A"][5:9]]
    assert weeks[0] == "10 jul"
    ja = _wb("ja")[STRINGS["ja"]["sheet_gantt"]].cell(2, 2).value
    assert "7月21日時点でリスクあり" in ja


# ------------------------------------------------------------- resolution

@pytest.mark.parametrize("given,want", [
    ("pt-BR", "pt"), ("pt_BR", "pt"), ("PT", "pt"), (" pt-br ", "pt"),
    ("es-MX", "es"), ("fr-CA", "fr"), ("ja-JP", "ja"), ("en-US", "en"),
    ("de", "en"), ("zh-Hans", "en"), ("", "en"), (None, "en"),
])
def test_locale_resolution(given, want):
    assert resolve_locale(given) == want
    assert DocStrings(given).locale == want


def test_a_missing_key_falls_back_to_english(monkeypatch):
    pt = dict(STRINGS["pt"])
    del pt["slip_title"]
    monkeypatch.setitem(doc_strings.STRINGS, "pt", pt)
    s = DocStrings("pt")
    assert s.t("slip_title") == STRINGS["en"]["slip_title"]
    assert s.t("sheet_slip") == "Desvios"          # the rest still pt
    assert s.label("Not A Declared Header") == "Not A Declared Header"


# ------------------------------------------------------ the contract lane

def test_contract_headers_and_cover_sheet_follow_the_locale():
    from app.services.artifact_types import BUDGET, plan_from_contract
    from app.services.workbook_plan import render_workbook
    emitted = {"filename": "zz.xlsx",
               "readme": {"purpose": "Zz proposito", "reviewers": ["Zz Ana"]},
               "sheets": [{"name": "Zz Estimativa", "rows": [
                   {"id": "B-1", "item": "Zz item", "low": 10, "high": 30}]},
                   {"name": None, "rows": [{"id": "B-2", "low": 1, "high": 2}]}]}
    plan = plan_from_contract(BUDGET, emitted, locale="pt-BR")
    assert plan["title"] == "Orçamento e estimativa de custos"
    labels = [c["label"] for c in plan["sheets"][0]["columns"]]
    assert labels == [LABELS["pt"][c.label] for c in BUDGET.columns]
    # keys are untouched: rows, totals and computed columns address them
    assert [c["key"] for c in plan["sheets"][0]["columns"]] == [
        c.key for c in BUDGET.columns]

    wb = openpyxl.load_workbook(io.BytesIO(
        render_workbook(plan, locale="pt-BR")))
    assert wb.sheetnames == ["LEIA-ME", "Zz Estimativa", "Planilha"]
    cover = wb["LEIA-ME"]
    assert cover["A1"].value == "Orçamento e estimativa de custos"
    assert [cover.cell(r, 1).value for r in (3, 5)] == ["Objetivo", "Revisores"]
    ws = wb["Zz Estimativa"]
    assert [c.value for c in ws[2]] == labels
    assert ws.cell(4, 1).value == "Total"            # pt says Total too
    assert str(ws.cell(4, 5).value).startswith("=IFERROR(SUM(")

    ja = openpyxl.load_workbook(io.BytesIO(render_workbook(
        plan_from_contract(BUDGET, emitted, locale="ja"), locale="ja")))
    assert ja.sheetnames[0] == "はじめに"
    assert ja["Zz Estimativa"].cell(4, 1).value == "合計"

    # and with no locale, exactly what it was
    en_plan = plan_from_contract(BUDGET, emitted)
    assert [c["label"] for c in en_plan["sheets"][0]["columns"]] == [
        c.label for c in BUDGET.columns]
    en = openpyxl.load_workbook(io.BytesIO(render_workbook(en_plan)))
    assert en.sheetnames == ["README", "Zz Estimativa", "Sheet"]


def test_the_contract_lane_hands_the_request_locale_to_both_halves():
    """Source-reading on purpose: the contract lane is dark by default and
    has no route-level harness. It can only say the argument is passed,
    not that the value is right; the Gantt lane's route tests below are
    what prove the value."""
    src = (ROOT / "app/routers/chat.py").read_text(encoding="utf-8")
    lane = src[src.index("_plan = plan_from_contract("):]
    lane = lane[:lane.index("_row = await _staging.stage(")]
    assert re.search(r"plan_from_contract\(_c, _emitted,\s*"
                     r"locale=_effective_locale\)", lane)
    assert re.search(r"render_workbook, _plan, locale=_effective_locale\)",
                     lane)


# -------------------------------------------- the route carries the locale

def _build_through_the_route(client, free_user, mock_provider, tmp_db_path,
                             monkeypatch, *, project_id, body_extra=None,
                             headers_extra=None):
    import json as _json
    import sqlite3
    from unittest.mock import AsyncMock

    import app.services.document_generation as dg
    from tests.conftest import chat_request
    from tests.test_document_generation import _enable_confirmed_generation
    from tests.test_gantt_detailed import _offer_gantt

    _enable_confirmed_generation(client)
    cta = _offer_gantt(client, free_user, monkeypatch, project_id=project_id)
    monkeypatch.setattr(dg, "interpret_offer_reply", AsyncMock(
        return_value={"confirm": True, "format": "xlsx", "style": "detailed"}))
    mock_provider.canned_response.text = _json.dumps(PLAN)
    mock_provider.return_value = mock_provider.canned_response
    r = client.post("/v1/chat", json={
        **chat_request(prompt_mode="ProjectChat", call_type="query",
                       user_content="detailed please"),
        "project_id": project_id,
        "metadata": {"offer_id": cta["details"]["offer_id"],
                     "generation_id": f"gen-{project_id}"},
        **(body_extra or {}),
    }, headers={**free_user["headers"], **(headers_extra or {})})
    assert r.status_code == 200, r.text
    con = sqlite3.connect(tmp_db_path)
    path = con.execute(
        "SELECT storage_path FROM generated_files WHERE user_id=?"
        " ORDER BY created_at DESC LIMIT 1",
        (free_user["user_id"],)).fetchone()[0]
    con.close()
    return openpyxl.load_workbook(io.BytesIO(open(path, "rb").read()))


def test_route_top_level_locale_reaches_the_renderer(
        client, free_user, mock_provider, tmp_db_path, monkeypatch):
    """What Shoulder Surf actually sends: a top-level `locale`, no
    metadata.locale and no Accept-Language (measured 2026-09-11)."""
    wb = _build_through_the_route(
        client, free_user, mock_provider, tmp_db_path, monkeypatch,
        project_id="proj-pt", body_extra={"locale": "pt-BR"})
    assert wb.sheetnames == _expected_tabs("pt")
    assert wb["Desvios"]["A1"].value == STRINGS["pt"]["slip_title"]


def test_route_accept_language_is_the_fallback(
        client, free_user, mock_provider, tmp_db_path, monkeypatch):
    wb = _build_through_the_route(
        client, free_user, mock_provider, tmp_db_path, monkeypatch,
        project_id="proj-fr",
        headers_extra={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
    assert wb.sheetnames == _expected_tabs("fr")


def test_route_without_any_locale_is_english(
        client, free_user, mock_provider, tmp_db_path, monkeypatch):
    wb = _build_through_the_route(
        client, free_user, mock_provider, tmp_db_path, monkeypatch,
        project_id="proj-en")
    assert wb.sheetnames == ["Gantt View", "Slip", "Receipts", "Progress"]
