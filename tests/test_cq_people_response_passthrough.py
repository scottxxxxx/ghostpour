"""A field CQ ADDS to a /v1/people row must reach the client untouched.

Rule 3 says a response-side test cannot see a request-side hole. This file is
the same sentence read the other way, and it exists because the existing
passthrough suite covers only the REQUEST direction: it proves an unmodelled
key reaches CQ, and nothing anywhere proves an unmodelled key CQ sends back
reaches the client.

Today it does. `_cq_proxy` returns CQ's parsed body with no response_model
anywhere in the file, so nothing can drop a key. That is a property of the
current implementation rather than a guarded one, which is exactly the state
the request-side suite was written about: passthrough achieved by nobody
having typed it yet is passthrough one refactor away from ending, silently,
with a 200 and a rendered row.

The occasion (CQ, 2026-09-02): they are ADDING `signals.days_present_7d`,
`signals.days_present_30d` and `signals.cadence.days_observed` alongside the
existing `meetings_7d`, `meetings_30d` and `cadence.meetings_observed`,
because SS found those count distinct DAYS PRESENT rather than meetings and
the old names are wrong. Both spellings serve for now. If GP ate the new
ones, both sides would be correct and the client would render the number
under the name that misdescribes it, for as long as anyone cared to look.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord
from app.routers import cq_proxy

USER = "user-people-1"


def _user(user_id=USER, tier="free"):
    return UserRecord(id=user_id, apple_sub="sub_people", tier=tier,
                      created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")


@pytest.fixture
def people_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _stub(payload, status=200):
    resp = MagicMock(); resp.status_code = status
    resp.json.return_value = payload; resp.text = ""
    inst = AsyncMock()
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=False)
    inst.request = AsyncMock(return_value=resp)
    return inst


@pytest.fixture
def cq_returns(monkeypatch):
    monkeypatch.setattr(cq_proxy, "get_settings",
                        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))
    holder = {}

    def _install(payload, status=200):
        holder["inst"] = _stub(payload, status)
        monkeypatch.setattr(cq_proxy.httpx, "AsyncClient",
                            lambda *a, **k: holder["inst"])
        return holder["inst"]
    return _install


# The row as CQ will serve it after their change: BOTH spellings present,
# nested one level deep, because a shallow copy would carry the top-level
# keys and lose the nested pair, and that is the failure worth catching.
PERSON_ROW = {
    "person_id": "p-1",
    "display_name": "Suresh",
    "meetings_7d": 3,
    "meetings_30d": 11,
    "signals": {
        "days_present_7d": 3,
        "days_present_30d": 11,
        "cadence": {
            "meetings_observed": 11,
            "days_observed": 11,
        },
    },
}
PEOPLE_PAYLOAD = {"people": [PERSON_ROW], "total": 1}


def test_a_person_row_crosses_byte_identical(people_client, cq_returns):
    cq_returns(PEOPLE_PAYLOAD)
    r = people_client.get(f"/v1/people/{USER}")
    assert r.status_code == 200
    assert r.json() == PEOPLE_PAYLOAD


@pytest.mark.parametrize("path", [
    ("signals", "days_present_7d"),
    ("signals", "days_present_30d"),
    ("signals", "cadence", "days_observed"),
])
def test_each_new_signal_field_survives_by_name(people_client, cq_returns, path):
    """Named individually so a failure says WHICH field was eaten. The
    byte-identical test above would go red for any of them and tell you
    nothing about which."""
    cq_returns(PEOPLE_PAYLOAD)
    got = people_client.get(f"/v1/people/{USER}").json()["people"][0]
    for key in path:
        assert key in got, f"GP DROPPED {'.'.join(path)} from the people row"
        got = got[key]
    assert got == 11 or got == 3


def test_the_old_and_new_spellings_both_arrive(people_client, cq_returns):
    """During the overlap both must cross. A proxy that carried only the
    names it had heard of would leave the client choosing between a value
    and nothing, which is how the misnamed one stays in use."""
    cq_returns(PEOPLE_PAYLOAD)
    row = people_client.get(f"/v1/people/{USER}").json()["people"][0]
    assert row["meetings_7d"] == row["signals"]["days_present_7d"]
    assert row["signals"]["cadence"]["meetings_observed"] == \
        row["signals"]["cadence"]["days_observed"]


def test_a_field_nobody_has_invented_yet_also_survives(people_client, cq_returns):
    """The general property, not the three fields of the day. This is what
    makes the file outlive the specific change that prompted it."""
    payload = {"people": [{**PERSON_ROW, "a_field_gp_has_never_heard_of": {
        "nested": ["and", "ordered"]}}], "total": 1}
    cq_returns(payload)
    got = people_client.get(f"/v1/people/{USER}").json()["people"][0]
    assert got["a_field_gp_has_never_heard_of"] == {"nested": ["and", "ordered"]}


def test_an_error_body_from_cq_crosses_intact(people_client, cq_returns):
    """The picker lesson: a middlebox that drops 4xx bodies breaks the
    client. A 404 whose body names the reason must arrive naming it."""
    cq_returns({"detail": {"code": "unknown_person", "person_id": "p-9"}}, status=404)
    r = people_client.get(f"/v1/people/{USER}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_person"
