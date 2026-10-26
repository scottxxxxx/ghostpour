"""Scott, 2026-10-25: reports leave out health details, and the report's
header title carries no person's name. Attendees and owners keep names."""
from app.services.meeting_report import REPORT_SYSTEM_PROMPT


def test_report_leaves_out_health_details_but_keeps_queries_verbatim():
    assert "Leave out health details in every narrative field" in REPORT_SYSTEM_PROMPT
    assert "(queries_during_meeting stays verbatim)" in REPORT_SYSTEM_PROMPT
    assert '"Sam is out until Friday", never why' in REPORT_SYSTEM_PROMPT


def test_report_title_carries_no_names():
    assert "The header title never contains a person's name or a health detail" in REPORT_SYSTEM_PROMPT
