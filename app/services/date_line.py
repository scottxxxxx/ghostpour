"""Today's date for Shoulder Surf's conversational calls.

2026-10-29, from the review of 681 real SS calls: none of the 210 distinct
system prompts carried a date, so a question that turns on it (who holds an
office now, how many days until the fifteenth) was answered from the model's
training cutoff. The chat path sends no timezone, so the date is UTC unless
the request's metadata names an IANA `timezone`, and the line says so.

Appended before the language directive, so RESPONSE LANGUAGE stays the last
thing in the prompt: the summary recipes say it "may appear at the end".
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CALL_TYPES = frozenset({
    "query", "query_follow_up",
    "meeting_chat", "meeting_chat_follow_up",
    "project_chat", "project_chat_follow_up",
})


def date_line(now: datetime, tz_name: str | None = None) -> str:
    zone, label = timezone.utc, "UTC; the user's local date may be a day either side"
    if tz_name:
        try:
            zone, label = ZoneInfo(tz_name), tz_name
        except Exception:
            pass
    local = now.astimezone(zone)
    return (
        "\n\n--- TODAY ---\n"
        f"Today is {local:%A} {local:%Y-%m-%d} ({label}). Use it for anything "
        "that depends on the date and to work out relative dates such as "
        "\"next Friday\". Do not mention it unless the answer needs it."
    )


def apply(system_prompt: str, app_id: str | None, call_type: str | None,
          now: datetime | None = None, tz_name: str | None = None) -> str:
    """`system_prompt` with today's date appended for SS conversational calls,
    unchanged for every other app and call type."""
    if app_id != "shouldersurf" or call_type not in CALL_TYPES or not system_prompt:
        return system_prompt
    return system_prompt + date_line(now or datetime.now(timezone.utc), tz_name)
