"""The Plus window cuts the MEETING CONTENT too, server-side, on the same dial.

Scott via CQ, 2026-08-26 (supersedes earlier): a Plus user's project chat
gets the last N days of meetings, sliding from today, even when the
project holds more; Pro has no window; N is the served dial, never a
constant. CQ windows memory patches with metadata.max_age_days; this is
GP's half for the client-assembled meeting blocks, which until now went
through whatever the slider selected (#739 left item 5 open).

Proved at the LLM boundary: the system prompt the provider receives.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.services.recall_window import clamp_meeting_blocks, recall_max_age_days

TODAY = date(2026, 8, 26)


def _block(i, n, d, title="T", body="- something said"):
    return f'## Meeting {i} of {n} — {d.isoformat()} · "{title}" (x ago)\n### Summary\n{body}\n\n'


def _prompt(dates, tail=""):
    n = len(dates)
    pre = f"You have context from {n} meeting(s), spanning {min(dates).isoformat()} to {max(dates).isoformat()}, ordered oldest → newest. For each meeting, the user has selected one or more of: summary, transcript excerpt, prior Q&A.\n\n"
    return "Project: P\n\n" + pre + "".join(_block(i + 1, n, d) for i, d in enumerate(dates)) + tail


# --- pure ----------------------------------------------------------------------

def test_blocks_older_than_the_window_are_dropped_and_the_preamble_is_restated():
    dates = [TODAY - timedelta(days=400), TODAY - timedelta(days=40), TODAY - timedelta(days=5)]
    out, dropped = clamp_meeting_blocks(_prompt(dates), 30, today=TODAY)
    assert dropped == [dates[0].isoformat(), dates[1].isoformat()]
    assert "Meeting 1 of 3" not in out and "Meeting 2 of 3" not in out
    assert 'Meeting 3 of 3 — 2026-08-21' in out          # numbering untouched
    assert "You have context from one meeting, dated 2026-08-21." in out
    assert "spanning" not in out


def test_the_boundary_is_inclusive_and_sliding_from_today():
    edge, past = TODAY - timedelta(days=30), TODAY - timedelta(days=31)
    out, dropped = clamp_meeting_blocks(_prompt([past, edge]), 30, today=TODAY)
    assert dropped == [past.isoformat()] and edge.isoformat() in out
    # the same prompt one day later: the edge meeting has aged out
    out2, dropped2 = clamp_meeting_blocks(_prompt([past, edge]), 30, today=TODAY + timedelta(days=1))
    assert dropped2 == [past.isoformat(), edge.isoformat()]
    assert "You have context from no meetings in the last 30 days." in out2


def test_the_window_is_the_dial_not_a_constant():
    dates = [TODAY - timedelta(days=40), TODAY - timedelta(days=5)]
    assert clamp_meeting_blocks(_prompt(dates), 45, today=TODAY)[1] == []
    assert clamp_meeting_blocks(_prompt(dates), 7, today=TODAY)[1] == [dates[0].isoformat()]


def test_no_window_no_blocks_or_a_bad_date_leaves_the_prompt_alone():
    dates = [TODAY - timedelta(days=400)]
    p = _prompt(dates)
    assert clamp_meeting_blocks(p, None, today=TODAY) == (p, [])
    assert clamp_meeting_blocks(p, 0, today=TODAY) == (p, [])
    assert clamp_meeting_blocks("no meetings here", 30, today=TODAY) == ("no meetings here", [])
    assert clamp_meeting_blocks(None, 30, today=TODAY) == (None, [])
    bad = '## Meeting 1 of 1 — 2026-13-45 · "T" (x)\n### Summary\n- kept\n'
    assert clamp_meeting_blocks(bad, 30, today=TODAY) == (bad, [])


def test_a_non_meeting_h2_after_the_last_block_survives():
    dates = [TODAY - timedelta(days=400), TODAY - timedelta(days=2)]
    out, dropped = clamp_meeting_blocks(_prompt(dates, tail="## House rules\n- be brief\n"), 30, today=TODAY)
    assert dropped == [dates[0].isoformat()] and "## House rules\n- be brief" in out


# --- at the LLM boundary -------------------------------------------------------

def _send(dates, prompt_mode="ProjectChat"):
    return {"provider": "anthropic", "model": "claude-haiku-4-5-20251001",
            "system_prompt": _prompt(dates), "user_content": "what did we decide?",
            "context_quilt": False,
            "metadata": {"prompt_mode": prompt_mode, "project_id": "p-1"}}


def _seen_system(mock_provider) -> str:
    assert mock_provider.call_args, "the provider was never called"
    return mock_provider.call_args.args[0].system_prompt


def _dates_for(client):
    from app.main import app as _app
    n = recall_max_age_days(_app.state.remote_configs, "plus")
    assert n, "the Plus dial must be set for this test to mean anything"
    today = date.today()
    return n, [today - timedelta(days=n * 3), today - timedelta(days=n + 1), today - timedelta(days=1)]


def test_a_plus_user_is_hydrated_with_the_last_n_days_only(client, plus_user, mock_provider):
    n, dates = _dates_for(client)
    r = client.post("/v1/chat", json=_send(dates), headers=plus_user["headers"])
    assert r.status_code == 200, r.text
    seen = _seen_system(mock_provider)
    assert dates[2].isoformat() in seen
    assert dates[0].isoformat() not in seen and dates[1].isoformat() not in seen
    assert "You have context from one meeting" in seen


def test_a_pro_user_keeps_every_meeting_the_slider_sent(client, pro_user, mock_provider):
    _, dates = _dates_for(client)
    r = client.post("/v1/chat", json=_send(dates), headers=pro_user["headers"])
    assert r.status_code == 200, r.text
    seen = _seen_system(mock_provider)
    assert all(d.isoformat() in seen for d in dates) and "3 meeting(s)" in seen


def test_the_clamp_is_project_chat_only(client, plus_user, mock_provider):
    _, dates = _dates_for(client)
    r = client.post("/v1/chat", json=_send(dates, prompt_mode="Summarize"), headers=plus_user["headers"])
    assert r.status_code == 200, r.text
    assert all(d.isoformat() in _seen_system(mock_provider) for d in dates)
