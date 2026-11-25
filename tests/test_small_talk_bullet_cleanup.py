from pathlib import Path

from app.services.text_hygiene import SMALL_TALK_BULLETS, drop_stray_small_talk_bullet as f

ES = "- Por ahora solo charla informal, aún no se habló de trabajo"


def test_dropped_when_work_bullets_follow():
    t = ES + "\n\nMigración de pedidos:\n- Javier pide el export a Logística"
    assert f(t) == "Migración de pedidos:\n- Javier pide el export a Logística"


def test_kept_when_it_is_the_whole_summary():
    for b in SMALL_TALK_BULLETS:
        assert f(b) == b


def test_untouched_without_it():
    t = "Decisions:\n- Ship Friday"
    assert f(t) == t


def test_every_bullet_matches_its_served_prompt():
    import json
    for loc, b in zip(("", ".es", ".fr", ".ja", ".pt"), SMALL_TALK_BULLETS, strict=True):
        sp = json.loads(Path(f"config/remote/protected-prompts{loc}.json").read_text())["summaryPrompts"]
        assert b in sp["full"], loc


def test_hooked_for_summary_calls():
    src = Path("app/routers/chat.py").read_text()
    assert 'call_type == "summary"' in src and "drop_stray_small_talk_bullet(response.text)" in src
