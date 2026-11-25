import json
from pathlib import Path

from app.services.meeting_title import clean_title

PP = json.loads(Path("config/remote/protected-prompts.json").read_text())["summaryPrompts"]


def test_every_summary_prompt_leads_with_the_bullet_format_and_bans_meta_notes():
    for mode in ("full", "delta", "consolidation"):
        p = PP[mode]
        assert p.index("OUTPUT FORMAT") < p.index("Prioritize") if "Prioritize" in p else True
        assert "YOU SUMMARIZE WHAT WAS SAID, NEVER THE RECORDING" in p
        assert '"- Small talk so far, no work discussed yet"' in p
        assert "—" not in p and "–" not in p


def test_the_update_prompt_checks_every_carried_point():
    assert "CHECK EVERY BULLET OF THE PREVIOUS SUMMARY" in PP["delta"]
    assert "output the previous summary again exactly as given" in PP["delta"]


def test_a_spaced_hyphen_in_a_title_becomes_a_colon():
    assert clean_title("Salon Lyon - Stand et Brochure") == "Salon Lyon: Stand et Brochure"
    assert clean_title("Q3 Plan — Budget") == "Q3 Plan: Budget"
    assert clean_title("Follow-up Pricing Rework") == "Follow-up Pricing Rework"


def test_each_locale_carries_the_same_change():
    small = {"es": "- Por ahora solo charla informal, aún no se habló de trabajo",
             "fr": "- Que des banalités pour l'instant, pas encore de travail abordé",
             "ja": "- 今のところ雑談のみで、仕事の話はまだ出ていない",
             "pt": "- Por enquanto só conversa informal, nenhum assunto de trabalho ainda"}
    for loc, bullet in small.items():
        sp = json.loads(Path(f"config/remote/protected-prompts.{loc}.json").read_text())["summaryPrompts"]
        for mode in ("full", "delta", "consolidation"):
            assert f'"{bullet}"' in sp[mode] or f"« {bullet} »" in sp[mode] or f"「{bullet}」" in sp[mode], (loc, mode)
            assert "—" not in sp[mode] and "–" not in sp[mode]
