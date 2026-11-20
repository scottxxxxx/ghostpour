from app.services.meeting_report import _deadline_not_in_task as f


def test_deadline_already_in_task_is_not_repeated():
    assert not f({"task": "Add Portuguese before Steven leaves on December 17th.", "deadline": "December 17th"})
    assert not f({"task": "Demo it over Thanksgiving.", "deadline": "thanksgiving"})


def test_deadline_missing_from_task_is_shown():
    assert f({"task": "Port the app to Android.", "deadline": "Friday"})


def test_no_deadline_shows_nothing():
    assert not f({"task": "x", "deadline": None})
