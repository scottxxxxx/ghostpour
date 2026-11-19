"""A bare yes to an exact read-back of a date she said earlier in the window
keeps it, however many lines back (Scott's 1.0.2 run, 2026-11-19, t_033)."""
import json

from app.services.n400_interviewer_guard import defer_dates_with_unspoken_day


def _turn(value):
    return json.dumps({"turn_id": "t_033", "facts": [
        {"field_id": "p8.trip2.date_returned", "value": value, "value_type": "date"}],
        "deferred": []})


def _conv(last_interviewer):
    return "\n".join([
        "INTERVIEWER: Any trips outside the United States?",
        "APPLICANT: Yes, I went to Mexico from December 20th, 2025 to January 3rd, 2023.",
        "INTERVIEWER: When did you leave for Mexico?",
        "APPLICANT: Sorry, I meant December 20th, 2022.",
        "INTERVIEWER: Any other trips?",
        "APPLICANT: No.",
        "INTERVIEWER: Is your income at or below 400 percent?",
        "APPLICANT: question",
        "INTERVIEWER: Do you want the reduced fee?",
        "APPLICANT: No",
        f"INTERVIEWER: {last_interviewer}",
        "APPLICANT: Yes.",
    ])


def _kept(value, last_interviewer):
    out, moved = defer_dates_with_unspoken_day(_turn(value), "Yes.", None, _conv(last_interviewer))
    return not moved


def test_yes_to_exact_readback_five_lines_back_is_kept():
    assert _kept("2023-01-03", "Back to your Mexico trip: you returned January 3rd, 2023, is that right?")


def test_minted_date_without_a_readback_is_still_refused():
    assert not _kept("2023-01-03", "How can USCIS reach you?")


def test_readback_of_a_date_she_never_said_is_refused():
    assert not _kept("2023-01-09", "You returned January 9th, 2023, is that right?")


def test_yes_to_readback_of_the_corrected_old_date_is_kept():
    # She said 2025-12-20 then corrected to 2022-12-20; an explicit read-back
    # of the OLD one plus her yes is hers. Without a read-back it is refused.
    assert _kept("2025-12-20", "You left December 20th, 2025, is that right?")
    assert not _kept("2025-12-20", "How can USCIS reach you?")
