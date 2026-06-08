"""Owner recovery for action-item chips.

When speaker diarization is ambiguous the analysis LLM labels the owner
"Multiple" (or leaves a raw "Speaker N"), even though the action text names
who committed ("Joy to validate ..."). _resolve_action_owner recovers the
real name(s) from the leading subject so the chip is useful. High precision:
fires only on a placeholder owner + a confident "Name(s) to|will <verb>" lead.
"""

from __future__ import annotations

import pytest

from app.services.meeting_report import _extract_owner_from_task, _resolve_action_owner


# Real action texts pulled from a production report that mislabeled owners.
@pytest.mark.parametrize(
    "owner,task,expected",
    [
        ("Multiple", "Joy to validate items 1 through 4 in the QA environment and report back on whether issues are still present.", "Joy"),
        ("Multiple", "Sukumar to review performance metrics data shared by Akhil this morning.", "Sukumar"),
        ("Multiple", "Srikanth to complete the new SDK installation and initiate deployment this week so it can be moved to production.", "Srikanth"),
        ("Multiple", "Vijay to review the API response format documentation and confirm what prefill logic is achievable.", "Vijay"),
    ],
)
def test_recovers_single_named_owner(owner, task, expected):
    assert _resolve_action_owner({"owner": owner, "task": task}) == expected


def test_recovers_two_named_owners_as_chip():
    a = {"owner": "Multiple", "task": "Priya and Sam to draft the integration spec by Friday."}
    assert _resolve_action_owner(a) == "Priya & Sam"


def test_recovers_from_raw_speaker_label():
    a = {"owner": "Speaker 3", "task": "Marcus to follow up with the security team on access."}
    assert _resolve_action_owner(a) == "Marcus"


def test_keeps_real_owner_untouched():
    """A genuine name owner is never second-guessed, even if the task also
    opens with a name."""
    a = {"owner": "Scott", "task": "Devin to review the PR."}
    assert _resolve_action_owner(a) == "Scott"


def test_no_recovery_when_lead_is_a_speaker_label():
    """'Speaker 4 to ...' has no real name lead — stays Multiple."""
    a = {"owner": "Multiple", "task": "Speaker 4 to set up a call with Devin from the security team."}
    assert _resolve_action_owner(a) == "Multiple"


def test_no_recovery_when_task_starts_with_a_verb():
    """No personal subject → no false positive."""
    a = {"owner": "Multiple", "task": "Monitor the cross-platform SPX issue in production for several more weeks."}
    assert _resolve_action_owner(a) == "Multiple"


def test_no_recovery_for_generic_team_lead():
    a = {"owner": "Multiple", "task": "Team to validate the release build before Friday."}
    assert _resolve_action_owner(a) == "Multiple"


def test_extract_returns_none_without_verb_pattern():
    assert _extract_owner_from_task("Akhil shared the metrics this morning.") is None


def test_extract_handles_empty():
    assert _extract_owner_from_task("") is None
    assert _resolve_action_owner({"owner": "Multiple", "task": ""}) == "Multiple"
