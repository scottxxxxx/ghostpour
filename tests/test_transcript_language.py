"""A Spanish meeting must not get an English refusal.

2026-08-21: a 66-second Spanish meeting, recorded through SS's language
picker, came back with "I would need a clear, legible transcript in
English" where its summary belonged. Nothing in the served summary recipe
named a language and nothing on the wire did either. Two layers fix it:
the served prompts now carry a language rule, and a client may STATE the
language as metadata.language (BCP-47), which GP turns into a server-side
line. These tests cover both, plus the report lane preferring the stated
language over the device locale.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.language_directive import (
    append_language_line, language_line, transcript_language)

ROOT = Path(__file__).parent.parent


# --- the served recipe -----------------------------------------------------

@pytest.mark.parametrize("loc", ["", ".es", ".fr"])
def test_every_transcript_bearing_served_prompt_carries_a_language_rule(loc):
    d = json.loads((ROOT / f"config/remote/protected-prompts{loc}.json").read_text())
    markers = ("LANGUAGE:", "IDIOMA:", "LANGUE :")
    for k, v in d["summaryPrompts"].items():
        assert any(m in v for m in markers), f"{loc} summaryPrompts.{k}"
        assert "another language" in v or "otro idioma" in v or "une autre langue" in v, f"{loc} {k} lacks the never-ask clause"
    for k in ("analysisPrompt", "analyzeSessionPrompt", "reanalyzeSummaryPrompt"):
        assert any(m in d[k] for m in markers), f"{loc} {k}"
    for dash in ("—", "–"):
        assert dash not in json.dumps(d, ensure_ascii=False), loc


# --- the stated language -----------------------------------------------------

def test_tag_validation_accepts_bcp47_and_rejects_junk():
    for ok in ("es", "es-MX", "pt-BR", "zh-Hant-TW", " fr "):
        assert transcript_language(ok) == ok.strip()
    for bad in (None, "", "  ", 7, "español", "en_US", "x" * 40, {"lang": "es"}):
        assert transcript_language(bad) is None, bad


def test_line_names_the_language_and_forbids_refusal():
    """The chat line keeps the FULL tag: es-US tells the model which
    variety was transcribed, and no lookup is keyed off it."""
    line = language_line("es-MX")
    assert "TRANSCRIPT LANGUAGE: es-MX" in line
    assert "never refuse" in line and "another language" in line


def test_append_leaves_the_prompt_alone_without_a_usable_tag():
    assert append_language_line("SYS", None) == "SYS"
    assert append_language_line("SYS", "español") == "SYS"
    assert append_language_line(None, None) is None


def test_append_puts_the_line_last_and_only_once():
    out = append_language_line("SYS\n\n", "es")
    assert out == "SYS\n\n" + language_line("es")
    assert append_language_line(None, "es") == language_line("es")


# --- through the chat route ---------------------------------------------------

def _spy(monkeypatch):
    import app.services.language_directive as ld
    seen = {}
    real = ld.append_language_line
    def spy(system_prompt, tag):
        out = real(system_prompt, tag); seen["out"] = out; return out
    monkeypatch.setattr(ld, "append_language_line", spy)
    return seen


def _send(client, user, metadata):
    body = {"provider": "anthropic", "model": "claude-haiku-4-5-20251001",
            "system_prompt": "You are a meeting summarizer.",
            "user_content": "Full meeting transcript:\nHola a todos", "metadata": metadata}
    resp = client.post("/v1/chat", json=body, headers=user["headers"])
    assert resp.status_code == 200, resp.text


def test_chat_appends_the_line_when_the_client_states_a_language(client, pro_user, monkeypatch):
    seen = _spy(monkeypatch)
    _send(client, pro_user, {"call_type": "summary", "transcript_language": "es"})
    assert seen["out"].endswith(language_line("es"))
    assert seen["out"].startswith("You are a meeting summarizer.")


def test_chat_is_unchanged_when_no_language_is_stated(client, pro_user, monkeypatch):
    seen = _spy(monkeypatch)
    _send(client, pro_user, {"call_type": "summary"})
    assert seen["out"] == "You are a meeting summarizer."


def test_the_device_language_key_does_not_trigger_the_line(client, pro_user, monkeypatch):
    """metadata.language means the DEVICE language (capture forwards it to
    CQ, which writes memory in it). An en-US phone recording a Spanish
    meeting must not be told the transcript is English."""
    seen = _spy(monkeypatch)
    _send(client, pro_user, {"call_type": "summary", "language": "en-US"})
    assert seen["out"] == "You are a meeting summarizer."


def test_chat_ignores_a_malformed_language_rather_than_failing(client, pro_user, monkeypatch):
    seen = _spy(monkeypatch)
    _send(client, pro_user, {"call_type": "summary", "transcript_language": "español"})
    assert seen["out"] == "You are a meeting summarizer."


# --- the report lane ----------------------------------------------------------

def test_report_locale_prefers_the_stated_language_over_the_device_locale():
    from app.services.language_directive import resolve_report_locale
    assert resolve_report_locale("es", "en-US,en;q=0.9") == "es"
    # SS sends the resolved regional locale; bundle lookups are keyed by
    # the bare code, so the resolver reduces it. "es-US" must find
    # report-strings.es, not miss it.
    assert resolve_report_locale("es-US", "en-US") == "es"
    assert resolve_report_locale("zh-Hant-TW", "en-US") == "zh"
    assert resolve_report_locale(None, "es-MX,es;q=0.9") == "es"
    assert resolve_report_locale("español", "fr-FR") == "fr"
    assert resolve_report_locale(None, None) in (None, "en")
