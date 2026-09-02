"""An unknown `patch_type` must reach the client verbatim.

CQ is registering a `behavior` patch type (their #243). Under the
additive contract it starts appearing in `/v1/quilt` sync with no route
change and nothing for GP to wire, which is exactly the shape where a
gateway quietly eats a key and the partner spends a day looking at their
own code. The people routes got this proof in #674; the quilt route,
which is the one `behavior` actually rides, did not have it.

These run the REAL `_cq_proxy` with only CQ's HTTP response stubbed. The
property under test is that GP has no patch_type vocabulary at all: it
never enumerates, filters, or reshapes, so a type invented after this
test was written passes through untouched.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import UserRecord
from app.routers import cq_proxy

USER = "user-quilt-1"


def _user(user_id: str = USER, tier: str = "free") -> UserRecord:
    return UserRecord(
        id=user_id, apple_sub="sub_quilt", tier=tier,
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def quilt_client(client):
    app.dependency_overrides[get_current_user] = lambda: _user()
    yield client
    app.dependency_overrides.pop(get_current_user, None)


def _cq_answers(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = ""
    instance = AsyncMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.request = AsyncMock(return_value=resp)
    return instance


@pytest.fixture
def cq_returns(monkeypatch):
    monkeypatch.setattr(
        cq_proxy, "get_settings",
        lambda: SimpleNamespace(cq_base_url="http://cq-mock"))

    def _install(payload, status=200):
        monkeypatch.setattr(
            cq_proxy.httpx, "AsyncClient",
            lambda *a, **k: _cq_answers(payload, status))
    return _install


# A behavior patch as CQ described it in #243: facet Episode, permanence
# quarter, not completable, not project scoped. Every field here is one
# GP has never heard of, which is the point.
BEHAVIOR_PATCH = {
    "patch_id": "p-behav-1",
    "patch_type": "behavior",
    "fact": "Restates the ask in their own words before agreeing",
    "facet": "Episode",
    "permanence": "quarter",
    "completable": False,
    "project_scoped": False,
    "entity_id": "ent-9",
    "created_at": "2026-08-16T10:00:00Z",
    "observation": {"count": 4, "spread_days": 21, "baseline_delta": 2.75},
}

QUILT_PAYLOAD = {
    "patches": [
        {"patch_id": "p-1", "patch_type": "decision",
         "fact": "Ship the queue change", "created_at": "2026-08-01T09:00:00Z"},
        BEHAVIOR_PATCH,
        # A type that does not exist anywhere yet. If the test only proved
        # "behavior" survives, it would be proving a hardcoded allowance.
        {"patch_id": "p-future", "patch_type": "not_invented_yet",
         "fact": "whatever CQ ships next", "nested": {"deep": [1, None, "x"]}},
    ],
    "server_time": "2026-08-16T12:00:00Z",
    "counts": {"total": 3},
}


def test_unknown_patch_types_reach_the_client_verbatim(quilt_client, cq_returns):
    cq_returns(QUILT_PAYLOAD)

    resp = quilt_client.get(f"/v1/quilt/{USER}")

    assert resp.status_code == 200
    assert json.loads(resp.content) == QUILT_PAYLOAD

    # Named explicitly so a future reshaping fails with a readable diff.
    got = resp.json()["patches"][1]
    assert got["patch_type"] == "behavior"
    assert got["facet"] == "Episode"
    assert got["permanence"] == "quarter"
    assert got["completable"] is False
    assert got["project_scoped"] is False
    assert got["observation"]["baseline_delta"] == 2.75
    assert resp.json()["patches"][2]["patch_type"] == "not_invented_yet"


def test_a_non_finite_number_is_the_one_thing_we_do_change(
        quilt_client, cq_returns, caplog):
    """Full disclosure for CQ: there IS exactly one transform on this
    path. A bare NaN/Infinity parses out of CQ's JSON but cannot be
    re-rendered, and one of them used to 502 the whole response, so we
    null it and log loudly. A behavior patch carrying a non-finite
    number arrives with that number as null, everything else intact."""
    payload = {"patches": [dict(BEHAVIOR_PATCH,
                                observation={"baseline_delta": float("nan")})]}
    cq_returns(payload)

    with caplog.at_level("WARNING"):
        resp = quilt_client.get(f"/v1/quilt/{USER}")

    assert resp.status_code == 200
    got = resp.json()["patches"][0]
    assert got["observation"]["baseline_delta"] is None
    assert got["patch_type"] == "behavior"
    assert got["facet"] == "Episode"
    assert "cq_proxy_non_finite_float" in caplog.text


def test_the_recall_block_labels_an_unknown_type_verbatim():
    """The other path a patch takes: into prompt context. `_format_patch`
    reads patch_type as an opaque label with a category fallback, so a
    new type is rendered, not dropped and not relabelled 'fact'."""
    from app.services.context_quilt import _format_patch

    line = _format_patch(BEHAVIOR_PATCH)
    assert line.startswith("[behavior] ")
    assert "Restates the ask in their own words" in line

    # The fallback chain still holds for a patch with no type at all.
    assert _format_patch({"category": "note", "fact": "x"}) == "[note] x"
    assert _format_patch({"fact": "x"}) == "[fact] x"


def test_gp_declares_no_patch_type_vocabulary():
    """The guarantee is the ABSENCE of code. If someone ever adds a
    patch_type enum, allowlist, or match statement to the proxy, this
    fails and they have to come read the docstring above first."""
    from pathlib import Path

    src = Path(cq_proxy.__file__).read_text()
    body = "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    )
    for forbidden in ("PATCH_TYPES", "ALLOWED_PATCH_TYPES", "patch_type =="):
        assert forbidden not in body, (
            f"{forbidden} in cq_proxy: the quilt route must stay a pure "
            "passthrough with no patch_type vocabulary")


# --- CQ #279: evidence-routed closures (asked 2026-08-17) ---------------
#
# CQ's worker used to archive every commitment extraction called
# resolved. Measured across all 167 on prod, most were not evidence of
# anything finishing: items closed because the promise was restated, or
# because somebody set an ETA. A close moves an item into the delivery
# history, so a wrong one shows a person as having delivered work they
# never did, and zero were ever reversed because the closures were never
# visible.
#
# Closures now route by evidence. Unambiguous ones close; the rest stay
# OPEN carrying a belief for a human to answer. These are the fields that
# carries on, and `believed_complete_reasons` is an ARRAY OF STRINGS,
# a shape this route has not carried before.

BELIEVED_ITEM = {
    "patch_id": "p-belief-1",
    "patch_type": "commitment",
    "fact": "Scott to add P2 and P3 demo scripts",
    "headline_mode": "believed_resolved",
    "believed_complete_at": "2026-08-15T20:35:23Z",
    "believed_complete_evidence": "I'll get those scripts over this week",
    "believed_complete_reasons": ["restated_promise", "eta_given"],
    "believed_complete_origin_id": "54E27791-7E97-4091-AC22-B2FE834675C1",
}


# ⚠ WHAT THIS FIXTURE DOES AND DOES NOT ESTABLISH (added 2026-08-19).
#
# It is a shape we ACCEPT, not a shape that was being sent. CQ found on
# 2026-08-19, via a local two-meeting simulation, that the believed_*
# family was never emitted on /v1/quilt at all: the worker stamped it and
# only the People detail route served it, so every confirm surface fed by
# the quilt sync starved by construction. Their PR #296 adds it.
#
# The tests below were true when written and are still true: they prove
# our hop does not eat these fields WHEN PRESENT, and GP holding no
# vocabulary for them is why no change was needed here.
#
# The claim they must not be read as proving is "the fields reach the
# device". Nobody proved that. Three teams each proved their own half,
# and the missing half had no owner: SS proved the render, we proved the
# passthrough, and the origin was never checked because a fixture on each
# side supplied it. This comment exists so the next reader takes the
# fixture as an assumption rather than as evidence about CQ.
#
# Rule 5, stated as a habit: name which side each claim was proved on.


def test_a_believed_completion_survives_the_proxy(quilt_client, cq_returns):
    """Every field is one GP has never heard of, including a list.

    Proves OUR half only. See the note above the fixture."""
    cq_returns({"patches": [BELIEVED_ITEM], "server_time": "2026-08-17T00:00:00Z"})
    got = quilt_client.get(f"/v1/quilt/{USER}").json()["patches"][0]
    assert got == BELIEVED_ITEM, "the proxy reshaped a believed-completion item"


def test_the_reasons_array_arrives_as_a_list(quilt_client, cq_returns):
    """A shape this route has not carried before. JSON is not
    automatically JSON once something in the middle owns a schema."""
    cq_returns({"patches": [BELIEVED_ITEM], "server_time": "2026-08-17T00:00:00Z"})
    reasons = quilt_client.get(
        f"/v1/quilt/{USER}").json()["patches"][0]["believed_complete_reasons"]
    assert isinstance(reasons, list), type(reasons)
    assert reasons == ["restated_promise", "eta_given"]


def test_completion_origin_id_survives_on_a_closed_item(quilt_client, cq_returns):
    closed = {"patch_id": "p-done-1", "patch_type": "commitment",
              "fact": "Ship it", "headline_mode": "completed",
              "completion_origin_id": "MTG-CLOSER-1"}
    cq_returns({"patches": [closed], "server_time": "2026-08-17T00:00:00Z"})
    got = quilt_client.get(f"/v1/quilt/{USER}").json()["patches"][0]
    assert got["completion_origin_id"] == "MTG-CLOSER-1"


def test_the_vouch_flag_survives(quilt_client, cq_returns):
    """The one most likely to be eaten: a single NEW KEY on an existing
    response body. SS needs it to tell a routine "still live" tap from a
    tap answering "looks done, confirm?" with a no. Same call, two
    meanings, and only this flag separates them, so if it is dropped the
    failure is silent on both sides of us.
    """
    cq_returns({"status": "ok", "patch_id": "p-1",
                "last_vouched_at": "2026-08-17T00:00:00Z",
                "cleared_believed_completion": True})
    got = quilt_client.post(f"/v1/quilt/{USER}/patches/p-1/vouch", json={}).json()
    assert got.get("cleared_believed_completion") is True, got
    assert got["status"] == "ok" and got["patch_id"] == "p-1"


def test_an_unknown_headline_mode_is_not_filtered(quilt_client, cq_returns):
    """`believed_resolved` is universal, so it can land on any ledger
    tracked object type. GP must have no mode vocabulary at all."""
    item = {"patch_id": "p-x", "patch_type": "not_invented_yet",
            "headline_mode": "some_mode_shipped_after_this_test"}
    cq_returns({"patches": [item], "server_time": "2026-08-17T00:00:00Z"})
    got = quilt_client.get(f"/v1/quilt/{USER}").json()["patches"][0]
    assert got["headline_mode"] == "some_mode_shipped_after_this_test"


# --- CQ #285: CONTESTED_NAME, a 409 whose BODY is the payload ---------
#
# `POST /v1/people/{u}` and `POST /v1/quilt/{u}/reassign-speaker` now
# REFUSE a typed name that could mean more than one live person instead
# of silently picking one, and the refusal carries the answer.
#
# This is the first 409 we forward whose body is DATA rather than an
# explanation, which is the case a gateway is most likely to break:
# preserving status while dropping or rewriting a 4xx body is a common
# and defensible-sounding thing for a middlebox to do. If the status
# crosses and the body does not, the client gets "contested" with
# nothing to render, which is worse than the behaviour it replaced. A
# wrong answer might get noticed; a dead end cannot be acted on at all.

CONTESTED = {
    "code": "CONTESTED_NAME",
    "name": "Mike",
    "candidates": [
        {"entity_id": "e-pete", "name": "Mike Peterson", "meetings": 1,
         "last_met": "2026-08-17", "projects": ["EMIDS"]},
        {"entity_id": "e-dit", "name": "Mike DiTroia", "meetings": 11,
         "last_met": "2026-08-17", "projects": ["Kore", "Cigna"]},
    ],
    "total": 3,
    "truncated": False,
}


def test_a_409_body_survives_on_create_person(quilt_client, cq_returns):
    """Status AND payload. Only 401 is rewritten on our side."""
    cq_returns(CONTESTED, status=409)
    r = quilt_client.post(f"/v1/people/{USER}", json={"name": "Mike"})
    assert r.status_code == 409, r.status_code
    assert r.json() == CONTESTED, "the 409 body was dropped or rewritten"


def _labels():
    return [{"label": "Speaker 5", "meeting_id": "E855"}]


def test_a_typed_name_reaches_cq_and_its_409_survives(quilt_client, cq_returns):
    """The gap that existed until 2026-08-18: our model carried no name
    field and the route demanded to_self XOR to_person_id, so a
    name-only send was refused by US with a 422 and CQ's 409 could never
    be produced here. `to_name` was not even new; the client had been
    speaking it for a week."""
    cq_returns(CONTESTED, status=409)
    r = quilt_client.post(f"/v1/quilt/{USER}/reassign-speaker",
                          json={"from_labels": _labels(), "to_name": "Mike"})
    assert r.status_code == 409, r.text[:200]
    assert r.json() == CONTESTED


def test_the_someone_new_retry_is_not_refused_as_ambiguous(
        quilt_client, cq_returns):
    """THE TRAP CQ NAMED. `create_new` is a MODIFIER on to_name, not a
    target, and the someone-new retry sends BOTH. Counting it as a
    target refuses exactly that retry, on the SECOND call, and only for
    users who picked "someone new" from the picker."""
    cq_returns({"patches_updated": 2}, status=200)
    r = quilt_client.post(f"/v1/quilt/{USER}/reassign-speaker", json={
        "from_labels": _labels(), "to_name": "Mike Peterson",
        "create_new": True})
    assert r.status_code == 200, r.text[:200]


def test_the_picked_person_retry_still_works(quilt_client, cq_returns):
    cq_returns({"patches_updated": 3}, status=200)
    r = quilt_client.post(f"/v1/quilt/{USER}/reassign-speaker", json={
        "from_labels": _labels(), "to_person_id": "e-dit"})
    assert r.status_code == 200


def test_create_new_alone_is_still_refused(quilt_client, cq_returns):
    """A modifier with nothing to modify is not a target."""
    cq_returns({}, status=200)
    r = quilt_client.post(f"/v1/quilt/{USER}/reassign-speaker", json={
        "from_labels": _labels(), "create_new": True})
    assert r.status_code == 422


def test_two_targets_are_still_refused(quilt_client, cq_returns):
    """Relaxing the rule must not relax it into accepting everything."""
    cq_returns({}, status=200)
    r = quilt_client.post(f"/v1/quilt/{USER}/reassign-speaker", json={
        "from_labels": _labels(), "to_name": "Mike", "to_self": True})
    assert r.status_code == 422


def test_the_name_actually_reaches_cq(quilt_client, cq_returns, monkeypatch):
    """Accepting the field is not forwarding it. Pins the wire."""
    seen = {}
    real = cq_proxy._cq_proxy

    async def _spy(method, path, body=None, query=None, **kw):
        # **kw and the pass-through are the point: a spy that drops an
        # argument it does not model IS the middlebox this file exists to
        # catch. When `request` was added for header forwarding, a spy
        # without **kw silently changed the call it was only meant to watch.
        seen["body"] = body
        return await real(method, path, body, query, **kw)
    monkeypatch.setattr(cq_proxy, "_cq_proxy", _spy)
    cq_returns({"patches_updated": 1}, status=200)
    quilt_client.post(f"/v1/quilt/{USER}/reassign-speaker", json={
        "from_labels": _labels(), "to_name": "Mike", "create_new": True})
    assert seen["body"]["to_name"] == "Mike"
    assert seen["body"]["create_new"] is True


def test_candidates_are_objects_with_their_nested_arrays(
        quilt_client, cq_returns):
    """One level deeper than the flat array of strings verified before,
    and the level where a schema-owning middlebox flattens things."""
    cq_returns(CONTESTED, status=409)
    got = quilt_client.post(f"/v1/people/{USER}", json={"name": "Mike"}).json()
    cands = got["candidates"]
    assert isinstance(cands, list) and all(isinstance(c, dict) for c in cands)
    projects = cands[1]["projects"]
    assert isinstance(projects, list), type(projects)
    assert projects == ["Kore", "Cigna"]


def test_the_candidate_ORDER_is_preserved(quilt_client, cq_returns):
    """THE ONE THAT FAILS INVISIBLY, and the reason CQ asked for a test
    rather than a code read.

    They rank server side because the client does not hold project
    membership. A re-serialisation that sorts or reorders leaves a
    picker that looks completely normal and is wrong: the best guess is
    no longer first, and nothing anywhere reports a problem.
    """
    cq_returns(CONTESTED, status=409)
    got = quilt_client.post(f"/v1/people/{USER}", json={"name": "Mike"}).json()
    assert [c["entity_id"] for c in got["candidates"]] == ["e-pete", "e-dit"], (
        "candidate ranking was reordered in transit; the picker will show "
        "the wrong person first and look fine doing it")
    # Nested order too: a sort deep in the tree is just as silent.
    assert got["candidates"][1]["projects"] == ["Kore", "Cigna"]


def test_key_order_within_a_candidate_is_untouched(quilt_client, cq_returns):
    """Weaker property than ranking, but a key-sorting serializer would
    break ranking too, so this catches the same class earlier."""
    cq_returns(CONTESTED, status=409)
    got = quilt_client.post(f"/v1/people/{USER}", json={"name": "Mike"}).json()
    assert list(got["candidates"][0].keys()) == list(
        CONTESTED["candidates"][0].keys())


# --- CQ #290: the rundown route, and a QUERY PARAM this time -----------
#
# `GET /v1/quilt/{u}?project_id=...&order=attention&limit=N` backs a
# project-level section for open items owed by somebody CQ could not
# place. Three of four on real data are JOINTLY owned ("Pradeep and
# Suresh" as one owner string), which a person-organised tab has no row
# for; one has been overdue since June and is invisible today.
#
# The response fields are the class already proved on /people. The QUERY
# PARAM is the one that matters, and its failure mode is silent and
# total: dropped, CQ receives no order, returns the recency default and
# answers 200, and the client renders a project header that looks
# entirely correct and contains almost none of the overdue work.
# Measured on ABM, the same top-40 request returns 1 overdue item
# without the flag and 40 with it.
#
# An unknown `order` value is a loud 422 on CQ's side. A DROPPED one is
# indistinguishable from a caller who never sent it, so it cannot be
# made loud from either endpoint. Only a request-side test on this hop
# can see it, which is the `to_name` lesson applied before it costs
# anything rather than after.

@pytest.fixture
def cq_spy(monkeypatch):
    """Records the URL we actually hand CQ."""
    seen: dict = {}

    def _install(payload, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload
        resp.text = ""

        async def _request(method, url, **kw):
            seen["method"] = method
            seen["url"] = str(url)
            return resp

        inst = AsyncMock()
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        inst.request = _request
        monkeypatch.setattr(cq_proxy, "get_settings",
                            lambda: SimpleNamespace(cq_base_url="http://cq-mock"))
        monkeypatch.setattr(cq_proxy.httpx, "AsyncClient",
                            lambda *a, **k: inst)
        return seen
    return _install


def test_the_order_attention_param_reaches_cq(quilt_client, cq_spy):
    """PROVED ON THE REQUEST SIDE, at the hop. Dropped, this degrades to
    a plausible and useless screen with a 200 on it."""
    seen = cq_spy({"patches": [], "server_time": "2026-08-18T00:00:00Z"})
    quilt_client.get(f"/v1/quilt/{USER}"
                     "?project_id=ABM&order=attention&limit=40")
    assert "order=attention" in seen["url"], (
        f"order was dropped in transit: {seen['url']}. CQ would answer 200 "
        "with the recency default and the header would look correct while "
        "containing almost none of the overdue work")


def test_every_param_survives_together(quilt_client, cq_spy):
    """Not one at a time. A rebuild that keeps the first and drops the
    rest passes a single-param test and fails in production."""
    seen = cq_spy({"patches": []})
    quilt_client.get(f"/v1/quilt/{USER}"
                     "?project_id=ABM&order=attention&limit=40&group_by=origin")
    url = seen["url"]
    for frag in ("project_id=ABM", "order=attention", "limit=40",
                 "group_by=origin"):
        assert frag in url, f"{frag} missing from {url}"


def test_an_absent_order_is_not_invented(quilt_client, cq_spy):
    """The other direction. We must not helpfully add a default the
    caller did not ask for, or a recency request silently becomes an
    attention one."""
    seen = cq_spy({"patches": []})
    quilt_client.get(f"/v1/quilt/{USER}?project_id=ABM")
    assert "order=" not in seen["url"], seen["url"]


def test_the_new_attention_fields_survive(quilt_client, cq_returns):
    """Same class as /people, confirmed on THIS route rather than
    reasoned across from the other one."""
    item = {
        "patch_id": "p-late", "patch_type": "commitment",
        "fact": "Pradeep and Suresh to land the ledger migration",
        "overdue_since": "2026-06-11T00:00:00Z",
        "salience": "high",
        "restatement_count": 3,
    }
    cq_returns({"patches": [item], "server_time": "2026-08-18T00:00:00Z"})
    got = quilt_client.get(f"/v1/quilt/{USER}").json()["patches"][0]
    assert got == item
    assert isinstance(got["restatement_count"], int)
    assert got["salience"] == "high"
