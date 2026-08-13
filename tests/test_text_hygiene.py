"""Dash hygiene backstop for served chat output.

Live 2026-08-12 (Scott's field test): a Project Chat answer shipped
several em dashes ("just say the word", "Project Plan", "This slipped"
all carried them) despite the template ban, because the injected context
is dash-heavy and models copy the punctuation they see. The prompt ban
was strengthened in the same change; this backstop normalizes whatever
still gets through, on the conversational surfaces only.
"""

from __future__ import annotations

import copy
import json as _json

from app.services.text_hygiene import normalize_dashes
from tests.conftest import chat_request
from tests.test_document_generation import _enable_confirmed_generation
from tests.test_gantt_detailed import _DPLAN


# --- unit: the helper ----------------------------------------------------

def test_spaced_em_dash_aside_becomes_commas():
    assert (normalize_dashes("The plan — as agreed — ships Friday")
            == "The plan, as agreed, ships Friday")


def test_unspaced_em_dash_break_becomes_comma():
    assert (normalize_dashes("This slipped—payments moved")
            == "This slipped, payments moved")


def test_en_dash_between_digits_stays_a_range():
    assert normalize_dashes("Jul 8–15, pages 3 – 5") == "Jul 8-15, pages 3-5"


def test_line_leading_dash_stays_a_bullet():
    assert (normalize_dashes("— item one\n– item two")
            == "- item one\n- item two")


def test_dash_hanging_at_line_end_becomes_bare_comma():
    assert normalize_dashes("This slipped —\nNext point") == "This slipped,\nNext point"
    assert normalize_dashes("Trailing —") == "Trailing,"


def test_field_case_phrases_normalize():
    # the three live phrases from the 2026-08-12 test
    assert (normalize_dashes("downloadable file — just say the word")
            == "downloadable file, just say the word")
    assert (normalize_dashes("Project Plan — our view")
            == "Project Plan, our view")
    assert (normalize_dashes("This slipped — payments moved a week")
            == "This slipped, payments moved a week")


def test_clean_text_and_empties_untouched():
    md = "A hyphenated follow-up, a range 3-5, and a - spaced hyphen stay."
    assert normalize_dashes(md) == md
    assert normalize_dashes("") == ""
    assert normalize_dashes(None) is None


# --- integration: wired into the non-stream chat response ----------------

def test_project_chat_answer_is_dash_normalized(client, free_user, mock_provider):
    mock_provider.return_value.text = (
        "Here is the plan — three phases.\nAuth – Jul 8–15.")
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        user_content="Current question: what is the plan?",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert r.json()["text"] == (
        "Here is the plan, three phases.\nAuth, Jul 8-15.")


def test_json_call_types_are_never_rewritten(client, free_user, mock_provider):
    # machine-parsed lanes keep their bytes; only the chat surfaces get
    # the backstop
    mock_provider.return_value.text = '{"result": "a — b"}'
    r = client.post("/v1/chat", json=chat_request(
        call_type="tr_match_analysis",
    ), headers=free_user["headers"])
    assert r.status_code == 200
    assert r.json()["text"] == '{"result": "a — b"}'


def test_template_extraction_keeps_evidence_quotes_verbatim(
        client, free_user, mock_provider, tmp_db_path, monkeypatch):
    """The receipts sheet quotes the meeting line behind every value; a
    normalization pass over the extraction JSON would silently rewrite
    the evidence. The backstop skips template turns entirely."""
    import io
    import sqlite3
    from unittest.mock import AsyncMock

    import openpyxl

    import app.services.document_generation as dg
    from app.services import generation_offers as go

    _enable_confirmed_generation(client)
    plan = copy.deepcopy(_DPLAN)
    quote = "payments — seventy percent, basically done"
    plan["tasks"][1]["evidence"][0]["quote"] = quote

    oid = go.create(free_user["user_id"], "xlsx", "plan",
                    template_id="gantt_detailed",
                    ask_content="build the detailed gantt")
    monkeypatch.setattr(dg, "interpret_offer_reply", AsyncMock(
        return_value={"confirm": True, "format": "xlsx", "style": None,
                      "version": None}))
    mock_provider.canned_response.text = _json.dumps(plan)
    mock_provider.return_value = mock_provider.canned_response
    r = client.post("/v1/chat", json=chat_request(
        prompt_mode="ProjectChat", call_type="query",
        metadata={"offer_id": oid, "generation_id": "gen-hygiene-1"},
        user_content="yes",
    ), headers=free_user["headers"])
    assert r.status_code == 200

    con = sqlite3.connect(tmp_db_path)
    path = con.execute(
        "SELECT storage_path FROM generated_files WHERE user_id=?"
        " ORDER BY created_at DESC LIMIT 1",
        (free_user["user_id"],)).fetchone()[0]
    con.close()
    wb = openpyxl.load_workbook(io.BytesIO(open(path, "rb").read()))
    found = any(
        isinstance(c.value, str) and quote in c.value
        for ws in wb.worksheets for row in ws.iter_rows() for c in row)
    assert found, "verbatim evidence quote (em dash intact) not in workbook"
