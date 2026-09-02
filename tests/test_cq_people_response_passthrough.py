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


# The real row shape, read off PROD on 2026-09-02 rather than imagined.
#
# An earlier version of this fixture invented it: `person_id`, `display_name`,
# and `meetings_7d` / `meetings_30d` at the TOP level. None of those exist.
# The identity key is `entity_id`, the name key is `name`, and BOTH spellings
# of the counts live inside `signals`. The passthrough property held either
# way, which is exactly why the error survived a green suite: a test that
# forwards an arbitrary dict cannot tell you the dict is fiction.
#
# Structure kept faithful because the nesting is the point: a shallow copy
# would carry `signals` and lose nothing, but a reshape that rebuilt
# `signals` from known keys would lose `cadence.days_observed` while leaving
# the row looking complete.
PERSON_ROW = {
    "entity_id": "ent-1",
    "name": "A Person",
    "patch_id": "477d19ff-1af1-4482-9904-d1dfd6d4ee1b",
    "confirmed": True,
    "meeting_count": 4,
    "signals": {
        "meetings_7d": 2,
        "meetings_30d": 4,
        "days_present_7d": 2,
        "days_present_30d": 4,
        "first_present_at": "2026-08-31",
        "last_present_at": "2026-08-31",
        "cadence": {
            "meetings_observed": 4,
            "days_observed": 4,
            "median_interval_days": 4,
        },
        "open_between": {
            "they_owe_open": 1,
            "they_owe_overdue": 0,
            "you_owe_open": None,
        },
        "turns_30d": None,
    },
}
# cadence is null on plenty of real rows (CQ: "stays null where it was
# null"), and a proxy that only ever saw the populated shape would not be
# exercised against it, so both are carried.
PERSON_ROW_NO_CADENCE = {
    "entity_id": "ent-2",
    "name": "Another Person",
    "meeting_count": 1,
    "signals": {
        "meetings_7d": 1,
        "meetings_30d": 1,
        "days_present_7d": 1,
        "days_present_30d": 1,
        "cadence": None,
    },
}
PEOPLE_PAYLOAD = {"people": [PERSON_ROW, PERSON_ROW_NO_CADENCE], "total": 2}


def test_a_person_row_crosses_byte_identical(people_client, cq_returns):
    cq_returns(PEOPLE_PAYLOAD)
    r = people_client.get(f"/v1/people/{USER}")
    assert r.status_code == 200
    assert r.json() == PEOPLE_PAYLOAD


@pytest.mark.parametrize("path,expected", [
    (("signals", "days_present_7d"), 2),
    (("signals", "days_present_30d"), 4),
    (("signals", "cadence", "days_observed"), 4),
])
def test_each_new_signal_field_survives_by_name(people_client, cq_returns,
                                                path, expected):
    """Named individually so a failure says WHICH field was eaten. The
    byte-identical test above would go red for any of them and tell you
    nothing about which."""
    cq_returns(PEOPLE_PAYLOAD)
    got = people_client.get(f"/v1/people/{USER}").json()["people"][0]
    for key in path:
        assert key in got, f"GP DROPPED {'.'.join(path)} from the people row"
        got = got[key]
    assert got == expected


def test_the_old_and_new_spellings_both_arrive(people_client, cq_returns):
    """During the overlap both must cross. A proxy that carried only the
    names it had heard of would leave the client choosing between a value
    and nothing, which is how the misnamed one stays in use."""
    cq_returns(PEOPLE_PAYLOAD)
    rows = people_client.get(f"/v1/people/{USER}").json()["people"]
    for row in rows:
        sig = row["signals"]
        assert sig["meetings_7d"] == sig["days_present_7d"]
        assert sig["meetings_30d"] == sig["days_present_30d"]
        cadence = sig.get("cadence")
        if cadence:
            assert cadence["meetings_observed"] == cadence["days_observed"]
    # Verified against prod 2026-09-02: across every row on a real account,
    # zero disagreements between the old and new spellings, and zero rows
    # missing days_present_7d.


def test_a_field_nobody_has_invented_yet_also_survives(people_client, cq_returns):
    """The general property, not the three fields of the day. This is what
    makes the file outlive the specific change that prompted it."""
    payload = {"people": [{**PERSON_ROW, "a_field_gp_has_never_heard_of": {
        "nested": ["and", "ordered"]}}], "total": 1}  # noqa
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


# ---------------------------------------------------------------------------
# CQ #397 (merged, prod d6fc288, 2026-09-02): three more added keys, and two
# of them are NOT on the list route.
#
#   presence.days_present          person DETAIL       (same as meetings_present)
#   days_since_last_statement      every item_ledger item (detail)
#   max_days_not_raised            item_ledger summaries (BOTH routes)
#
# ⚠ PROVENANCE OF THESE FIXTURES, stated because the last one I did not state
# was wrong. The list fixture above was corrected against a REAL prod response.
# The detail fixture below is built from CQ's key lists READ OUT OF THEIR
# SOURCE, which is one step short of bytes: it is right about names and
# nesting and says nothing about values or nulls. Verifying it against a real
# detail response needs another prod read on a real account, which is Scott's
# call and is pending. Until then these prove GP does not drop what it is
# handed, in the shape CQ says it hands it.
#
# The rollups on the two routes are DIFFERENT OBJECTS, which is the trap here:
#   list   item_ledger_rollup = {scope, people_considered, people_with_items,
#                               summary, by_person[]}, receipts STRIPPED
#   detail item_ledger        = {scope, items[], summary, vocabulary},
#                               receipts PRESENT
# So `max_days_not_raised` has THREE homes, and a fixture that only covered
# one would leave two untested while looking done.
# ---------------------------------------------------------------------------

ENTITY = "ent-1"

_SUMMARY_STRIPPED = {
    "items": 3,
    "by_mode": {"they_owe": 2, "you_owe": 1},
    "median_hop_count": 2,
    "max_hop_count": 5,
    "not_raised_since": "2026-08-20",
    "max_meetings_not_raised": 4,
    "max_days_not_raised": 4,
    "raised_with_a_question": 1,
    "raised_without_advance": 2,
    "items_raised_without_advance": 1,
    "max_raised_without_advance_on_one_item": 2,
    "raised_unmeasurable": 0,
    "raised_definition": "restated without a new commitment",
    "advance_definition": "a new deadline, owner, or scope",
}
# The detail summary is the same object WITH the two receipt keys.
_SUMMARY_WITH_RECEIPTS = {
    **_SUMMARY_STRIPPED,
    "patch_ids_by_mode": {"they_owe": ["p-1", "p-2"], "you_owe": ["p-3"]},
    "patch_ids_raised_without_advance": ["p-1"],
}

LEDGER_ITEM = {
    "patch_id": "p-1",
    "object_type": "commitment",
    "text": "Send the revised sheet",
    "owner": "them",
    "origin_id": "MTG-7",
    "project_id": None,
    "mode": "they_owe",
    "modes": ["they_owe"],
    "hop_count": 2,
    "deadline_moves": 0,
    "deadline": None,
    "deadline_date": None,
    "overdue_since": None,
    "first_stated_on": "2026-08-18",
    "last_stated_on": "2026-08-20",
    "days_open": 15,
    "meetings_since_last_statement": 4,
    "days_since_last_statement": 4,
    "owner_change": False,
    "raised_with_a_question": True,
    "object_regression": False,
    "completed_at": None,
    "restatements": [{"origin_id": "MTG-7", "stated_on": "2026-08-20"}],
    "deadline_history": [],
}

PERSON_DETAIL = {
    "entity_id": ENTITY,
    "name": "A Person",
    "presence": {
        "first_present_at": "2026-08-18",
        "last_present_at": "2026-08-31",
        "meetings_present": 4,
        "days_present": 4,
    },
    "item_ledger": {
        "scope": "open_only",
        "items": [LEDGER_ITEM],
        "summary": _SUMMARY_WITH_RECEIPTS,
        "vocabulary": {"they_owe": "they owe you"},
    },
}

LIST_WITH_ROLLUP = {
    **PEOPLE_PAYLOAD,
    "item_ledger_rollup": {
        "scope": "open_only",
        "people_considered": 380,
        "people_with_items": 12,
        "summary": _SUMMARY_STRIPPED,
        "by_person": [
            {"entity_id": ENTITY, "name": "A Person", "questions": 1,
             **_SUMMARY_STRIPPED},
        ],
    },
}


def test_the_detail_route_crosses_byte_identical(people_client, cq_returns):
    cq_returns(PERSON_DETAIL)
    r = people_client.get(f"/v1/people/{USER}/{ENTITY}")
    assert r.status_code == 200
    assert r.json() == PERSON_DETAIL


@pytest.mark.parametrize("path,expected", [
    (("presence", "days_present"), 4),
    (("item_ledger", "summary", "max_days_not_raised"), 4),
    (("item_ledger", "items", 0, "days_since_last_statement"), 4),
])
def test_each_new_detail_field_survives_by_name(people_client, cq_returns,
                                                path, expected):
    cq_returns(PERSON_DETAIL)
    got = people_client.get(f"/v1/people/{USER}/{ENTITY}").json()
    for key in path:
        if isinstance(key, int):
            assert isinstance(got, list) and len(got) > key, \
                f"GP DROPPED {path}: no element {key}"
        else:
            assert isinstance(got, dict) and key in got, \
                f"GP DROPPED {path}: missing {key!r}"
        got = got[key]
    assert got == expected


def test_the_detail_receipts_are_not_stripped_by_the_hop(people_client, cq_returns):
    """Receipts appear on the detail summary and NOT on the list rollup. GP
    must not normalise the two into one shape: which keys are present is
    CQ's decision about that route, and a proxy that made them agree would
    be inventing a contract."""
    cq_returns(PERSON_DETAIL)
    summary = people_client.get(
        f"/v1/people/{USER}/{ENTITY}").json()["item_ledger"]["summary"]
    assert summary["patch_ids_by_mode"] == {
        "they_owe": ["p-1", "p-2"], "you_owe": ["p-3"]}
    assert summary["patch_ids_raised_without_advance"] == ["p-1"]


def test_max_days_not_raised_survives_in_all_three_homes(people_client, cq_returns):
    """It lives in the list rollup summary, in every by_person entry on the
    list, and in the detail summary. A fixture covering one would leave two
    untested while looking done."""
    cq_returns(LIST_WITH_ROLLUP)
    body = people_client.get(f"/v1/people/{USER}").json()
    assert body["item_ledger_rollup"]["summary"]["max_days_not_raised"] == 4
    assert body["item_ledger_rollup"]["by_person"][0]["max_days_not_raised"] == 4

    cq_returns(PERSON_DETAIL)
    detail = people_client.get(f"/v1/people/{USER}/{ENTITY}").json()
    assert detail["item_ledger"]["summary"]["max_days_not_raised"] == 4


def test_the_list_rollup_stays_stripped_of_receipts(people_client, cq_returns):
    """The other direction of the same rule: counts on the list, receipts on
    the detail. If GP ever grew a shared model for 'summary' it would leak
    patch ids onto the list route, which is a disclosure rather than a drop."""
    cq_returns(LIST_WITH_ROLLUP)
    summary = people_client.get(
        f"/v1/people/{USER}").json()["item_ledger_rollup"]["summary"]
    assert "patch_ids_by_mode" not in summary
    assert "patch_ids_raised_without_advance" not in summary


# --- route ordering ---------------------------------------------------------
# Both teams noted this as untested (GP) and unverified (CQ) on 2026-09-02,
# which is two people agreeing something is unchecked rather than checking it.
# It is one test.
#
# `/people/{user_id}/network` is declared BEFORE `/people/{user_id}/{entity_id}`
# in cq_proxy.py, with a comment saying the ordering is insurance. Insurance
# nobody has ever claimed on is indistinguishable from an assumption: if the
# catch-all won, `network` would be forwarded as a person whose entity_id is
# the literal string "network", CQ would answer 404 for an unknown entity, and
# it would read as a client bug on a route GP does carry.

def _forwarded_path(stub) -> str:
    """The path GP actually sent upstream, from the stub's recorded call."""
    args, kwargs = stub.request.call_args
    return args[1] if len(args) > 1 else kwargs["url"]


def test_network_is_not_swallowed_by_the_entity_catch_all(people_client, cq_returns):
    """Asserts WHICH HANDLER RAN, not the path string.

    The first version of this test asserted the forwarded path ended in
    "/network" and had four segments. It passed under sabotage. Both
    handlers build the identical upstream path when entity_id is the literal
    "network", which cq_proxy's own docstring says out loud: "Today both
    would build the same upstream path by accident." A string assertion
    cannot tell two handlers apart when they agree on the string.

    The behavioural discriminator is the QUERY. `get_people_network` takes no
    query parameters and forwards none; `get_person` forwards
    `request.url.query` verbatim. So a request carrying a query string is
    answered differently by the two, and that difference is real behaviour
    rather than a coincidence of formatting.
    """
    stub = cq_returns({"nodes": [], "edges": []})
    people_client.get(f"/v1/people/{USER}/network?since=2026-01-01")
    path = _forwarded_path(stub)
    assert path.endswith("/network"), path
    assert "since=" not in path, (
        "the entity catch-all answered /network: it forwarded the query "
        f"string, which get_people_network never does. Got: {path}")


def test_the_router_resolves_network_to_its_own_handler(people_client, cq_returns):
    """Second instrument, re-derived a different way.

    The query test above is behavioural and could itself go blind if either
    handler's query handling changed. This one asks the router directly which
    endpoint owns the path, so the two checks fail for different reasons and
    a single mistake cannot silence both.
    """
    from app.main import app as fastapi_app

    def _endpoint_for(path: str) -> str:
        for route in fastapi_app.routes:
            match, _ = route.matches({"type": "http", "method": "GET",
                                      "path": path, "path_params": {},
                                      "root_path": "", "headers": []})
            if match.name == "FULL":
                return route.endpoint.__name__
        return "<no route>"

    assert _endpoint_for(f"/v1/people/{USER}/network") == "get_people_network"
    assert _endpoint_for(f"/v1/people/{USER}/{ENTITY}") == "get_person"


def test_a_real_entity_id_still_reaches_the_detail_path(people_client, cq_returns):
    """The other half. A test that only pinned `network` would pass on a
    router that had stopped serving detail entirely."""
    stub = cq_returns(PERSON_DETAIL)
    people_client.get(f"/v1/people/{USER}/{ENTITY}")
    assert _forwarded_path(stub).endswith(f"/{ENTITY}")


# ---------------------------------------------------------------------------
# REQUEST SIDE: Accept-Language must reach CQ.
#
# CQ shipped per-locale strings on the people routes (their #406, prod
# 750a4b1) reading `Accept-Language`. GP's proxy BUILDS its outbound headers
# rather than copying them, so every proxied call reached CQ headerless and
# every user got English no matter what the client sent. Because CQ's
# headerless output is deliberately byte-identical to the old output, it
# looked like the feature working correctly FROM BOTH ENDS.
#
# Nothing on either side could have caught it. CQ's tests prove their
# writer; ours proved our response passthrough. The hole was on the request
# hop, and a response-side test cannot see a request-side hole (rule 3).
# It was found by CQ ASKING what our proxy does with the header.
#
# So these are REQUEST-side: they assert what GP SENT, read off the stub's
# recorded outbound call, not what came back.
# ---------------------------------------------------------------------------

def _sent_headers(stub) -> dict:
    _, kwargs = stub.request.call_args
    return {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}


@pytest.mark.parametrize("locale", ["es", "fr", "ja", "es-MX,es;q=0.9,en;q=0.8"])
def test_accept_language_reaches_cq_on_the_list_route(people_client, cq_returns,
                                                     locale):
    stub = cq_returns(PEOPLE_PAYLOAD)
    people_client.get(f"/v1/people/{USER}", headers={"Accept-Language": locale})
    sent = _sent_headers(stub)
    assert sent.get("accept-language") == locale, (
        "GP did not forward Accept-Language; CQ localisation is inert and "
        "every user gets English while both sides look correct")


def test_accept_language_reaches_cq_on_the_detail_route(people_client, cq_returns):
    """The detail route is where CQ's #406 actually localises the subject
    strings, so pinning the list alone would leave the real one untested."""
    stub = cq_returns(PERSON_DETAIL)
    people_client.get(f"/v1/people/{USER}/{ENTITY}",
                      headers={"Accept-Language": "ja"})
    assert _sent_headers(stub).get("accept-language") == "ja"


def test_the_value_is_forwarded_verbatim_not_reinterpreted(people_client, cq_returns):
    """GP parses Accept-Language for its own config resolution. It must not
    hand CQ its parsed opinion: CQ has its own rules and a normalised `es`
    would quietly lose a q-weighted preference list."""
    raw = "fr-CA,fr;q=0.9,en;q=0.5"
    stub = cq_returns(PEOPLE_PAYLOAD)
    people_client.get(f"/v1/people/{USER}", headers={"Accept-Language": raw})
    assert _sent_headers(stub).get("accept-language") == raw


def test_a_headerless_caller_sends_no_accept_language(people_client, cq_returns):
    """Byte-identical for headerless callers, which is what CQ promised. GP
    must not invent a default and turn 'no preference' into a choice."""
    stub = cq_returns(PEOPLE_PAYLOAD)
    people_client.get(f"/v1/people/{USER}")
    assert "accept-language" not in _sent_headers(stub)


def test_only_the_allowlisted_headers_cross(people_client, cq_returns):
    """The allowlist is the contract. A proxy that copied every client header
    would leak the caller's Authorization to CQ, which is a different and
    worse bug than the one being fixed."""
    from app.routers.cq_proxy import _FORWARDED_REQUEST_HEADERS
    stub = cq_returns(PEOPLE_PAYLOAD)
    people_client.get(f"/v1/people/{USER}",
                      headers={"Accept-Language": "es",
                               "X-Sneaky-Header": "should-not-cross"})
    sent = _sent_headers(stub)
    assert "x-sneaky-header" not in sent
    assert _FORWARDED_REQUEST_HEADERS == ("accept-language",), (
        "the allowlist grew; each entry wants its own request-side test")


def test_a_client_cannot_override_the_server_auth_header(people_client, cq_returns):
    """Auth is applied LAST on purpose. If a client could set Authorization
    and have it forwarded, it would be talking to CQ as GP."""
    stub = cq_returns(PEOPLE_PAYLOAD)
    people_client.get(f"/v1/people/{USER}",
                      headers={"Authorization": "Bearer not-the-server-token"})
    sent = _sent_headers(stub)
    assert sent.get("authorization") != "Bearer not-the-server-token"
