"""The Woven memory digest: GP caches it and joins nothing.

Read off `Memory Quilt.dc.html` in Scott's design project rather than off a
summary of it, which matters because three of the things pinned here did not
survive the trip through prose.

GP's whole job on this surface is caching. It does NOT join meeting titles,
durations or minute marks into the body even though §5 asks for them: CQ
serves no meeting titles and keeps no transcript spans, and GP holds no
titles either (meeting_transcripts, meeting_reports and usage_log all carry
meeting_id and none carry a title). The device joins on source_meeting_id
against its own MeetingRecord.
"""

from __future__ import annotations

import pytest

import app.routers.cq_proxy as cq
from app.services import woven_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    woven_cache.clear()
    yield
    woven_cache.clear()


def _digest(n_tiles: int = 6, total: int = 2770, meetings: int = 41) -> dict:
    """A digest shaped like the design's own data."""
    return {
        "total_memories": total,
        "meetings_count": meetings,
        "since": "2026-03-01",
        "patches": [
            {"patch_id": f"p{i}", "patch_type": "takeaway",
             "fact": "Target market of 60-67% small firms is ideal.",
             "headline": "60-67% small firms is the sweet spot",
             "weight": 3, "span": 3, "height": 118,
             "source_meeting_id": "MEET-1", "occurred_at": "2026-08-28T16:11:00Z"}
            for i in range(n_tiles)
        ],
        "thread": {"source_meeting_id": "MEET-1", "patch_count": 6},
    }


def _capture(monkeypatch, body=None, status=200):
    seen = {"n": 0, "paths": [], "queries": []}
    payload = body if body is not None else _digest()

    async def _fake(method, path, *a, **kw):
        from fastapi.responses import JSONResponse
        seen["n"] += 1
        seen["paths"].append(path)
        seen["queries"].append(kw.get("query"))
        return JSONResponse(status_code=status, content=payload)

    monkeypatch.setattr(cq, "_cq_proxy", _fake)
    return seen


def _h(user):
    return {**user["headers"], "X-App-ID": "shouldersurf"}


# --- the two time bases, which is the easiest thing to get wrong ---

def test_lifetime_totals_and_a_windowed_selection_coexist(
    client, free_user, monkeypatch
):
    """CQ asked for this assertion specifically.

    The header reads "SINCE MARCH · 41 MEETINGS" and "2,770 things you'd
    have forgotten" while the grid below is "THIS WEEK'S PATCHES" from
    window=7d. So total_memories, meetings_count and since are ALL-TIME and
    only `patches` is windowed. One response, two time bases.

    This is exactly the shape someone later "fixes" into consistency, and
    the fix guts the opening claim by shrinking the headline to a weekly
    count. The invariant is cheap: the lifetime number must exceed the tile
    count.
    """
    _capture(monkeypatch)
    r = client.get("/v1/memory/woven?window=7d&limit=6", headers=_h(free_user))
    assert r.status_code == 200
    b = r.json()

    assert b["total_memories"] == 2770
    assert b["meetings_count"] == 41
    assert b["since"] == "2026-03-01"
    assert len(b["patches"]) == 6
    assert b["total_memories"] > len(b["patches"]), (
        "the lifetime total collapsed to the windowed count; the header "
        "number is the whole opening claim of the screen"
    )


def test_the_window_reaches_cq_and_does_not_touch_the_totals(
    client, free_user, monkeypatch
):
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven?window=30d&limit=4&project_id=PROJ-1",
               headers=_h(free_user))
    q = seen["queries"][0]
    assert "window=30d" in q and "limit=4" in q and "project_id=PROJ-1" in q


def test_the_project_rename_across_the_hop_is_explicit(
    client, free_user, monkeypatch
):
    """The §5 spec spells it `project`; CQ takes `project_id`.

    A rename across a hop is the MISNAMING half of the typed-hop class, the
    half with no instrument, so it gets a test rather than a quiet query
    string. Both spellings are accepted and both forward under CQ's name.

    The value must be an ID either way. Forwarding a project NAME as
    project_id returns an empty digest with a 200, which is the silent
    shape this class keeps taking.
    """
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven?project=PROJ-9", headers=_h(free_user))
    assert "project_id=PROJ-9" in seen["queries"][0]
    assert "project=PROJ-9" not in seen["queries"][0].replace("project_id=", "")


def test_it_forwards_to_CQS_path_not_an_invented_one(
    client, free_user, monkeypatch
):
    """A proxy pointed at a path that does not exist 404s at CQ and reads
    as a CQ bug from here."""
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven", headers=_h(free_user))
    client.get("/v1/memory/meetings/MEET-7/woven", headers=_h(free_user))
    assert seen["paths"][0].endswith("/woven")
    assert "/v1/quilt/" in seen["paths"][0]
    assert seen["paths"][1].endswith("/meetings/MEET-7/woven")
    assert "/v1/quilt/" in seen["paths"][1]


@pytest.mark.parametrize("raw,expect", [
    ("4", "limit=4"), ("banana", "limit=6"), ("", "limit=6"),
    ("0", "limit=1"), ("99", "limit=6"),
])
def test_a_bad_limit_never_4xxs(raw, expect, client, free_user, monkeypatch):
    """CQ made `window` fall back rather than reject: a typo must not cost
    a user their memory tab on a browse surface. A plain `int` annotation
    here would have FastAPI 422 the request before their tolerance ever
    ran, so my signature would have defeated their decision."""
    seen = _capture(monkeypatch)
    r = client.get(f"/v1/memory/woven?limit={raw}", headers=_h(free_user))
    assert r.status_code == 200, f"limit={raw!r} produced {r.status_code}"
    assert expect in seen["queries"][0]


def test_a_bad_window_never_4xxs(client, free_user, monkeypatch):
    _capture(monkeypatch)
    r = client.get("/v1/memory/woven?window=%%%", headers=_h(free_user))
    assert r.status_code == 200


def test_the_seam_keeps_CAPTURE_ORDER(client, free_user, monkeypatch):
    """CQ returns a meeting's patches in capture order, NOT ranked, and GP
    must not reorder. The screen exists to walk a meeting as it happened;
    a ranked timeline is a different screen nobody asked for. 'Sort the
    patches' is an obvious-looking improvement, hence a test."""
    ordered = {"meeting_id": "MEET-1", "dropped": {}, "patches": [
        {"patch_id": "c", "patch_type": "goal", "fact": "third"},
        {"patch_id": "a", "patch_type": "takeaway", "fact": "first"},
        {"patch_id": "b", "patch_type": "blocker", "fact": "second"},
    ]}
    _capture(monkeypatch, body=ordered)
    b = client.get("/v1/memory/meetings/MEET-1/woven",
                   headers=_h(free_user)).json()
    assert [p["patch_id"] for p in b["patches"]] == ["c", "a", "b"]


def test_dropped_survives_the_hop(client, free_user, monkeypatch):
    """An empty quilt with {'sensitive_content': 4} behind it is a very
    different product state from an empty quilt with nothing behind it, and
    only one of them is a bug. Swallowing it at this hop would erase the
    distinction."""
    _capture(monkeypatch, body={**_digest(0), "dropped": {"sensitive_content": 4}})
    b = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    assert b["dropped"] == {"sensitive_content": 4}


# --- GP joins nothing ---

def test_gp_does_not_invent_titles_durations_or_minute_marks(
    client, free_user, monkeypatch
):
    """The boundary, asserted rather than commented.

    ⚠ meeting_shares HAS title, meeting_date and duration_seconds and sits
    exactly where a join would go. It is a trap: that row exists only when a
    user SHARED a meeting, so it covers a self-selected subset and is a
    snapshot that goes stale on the first rename. Joining on it would give a
    real title for a handful and null for the rest, so "no title" and "never
    shared" become one observable.
    """
    _capture(monkeypatch)
    b = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    for p in b["patches"]:
        assert "source_meeting_id" in p, "the device needs the join key"
        for invented in ("source_meeting_title", "meeting_title",
                         "duration_seconds", "minute_mark"):
            assert invented not in p, (
                f"GP served {invented!r}; it holds no such data and a "
                f"partial answer is worse than none"
            )


# --- caching: day-stable, stale-while-revalidate ---

def test_a_second_open_does_not_refetch(client, free_user, monkeypatch):
    """The point of the cache. Two opens of the tab in the same day are one
    fan-out, and the tiles cannot reshuffle between them."""
    seen = _capture(monkeypatch)
    first = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    second = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    assert seen["n"] == 1, f"refetched {seen['n']} times"
    assert first["patches"] == second["patches"], "the tiles moved mid-day"


def test_freshness_is_on_every_response_not_only_stale_ones(
    client, free_user, monkeypatch
):
    """`as_of` ships whether or not the digest is stale, so the client has
    one code path. With stale-while-revalidate the "as of" state is the
    NORMAL path; a field that appeared only on failure would be the rarer
    and therefore less-tested branch."""
    _capture(monkeypatch)
    b = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    assert b["_freshness"]["stale"] is False
    assert b["_freshness"]["as_of"]


def test_a_different_project_is_a_different_digest(
    client, free_user, monkeypatch
):
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven?project=Cigna", headers=_h(free_user))
    client.get("/v1/memory/woven?project=Vet", headers=_h(free_user))
    assert seen["n"] == 2, "two projects shared one cache entry"


def test_a_meeting_digest_is_cached_separately_per_meeting(
    client, free_user, monkeypatch
):
    seen = _capture(monkeypatch)
    client.get("/v1/memory/meetings/MEET-1/woven", headers=_h(free_user))
    client.get("/v1/memory/meetings/MEET-1/woven", headers=_h(free_user))
    client.get("/v1/memory/meetings/MEET-2/woven", headers=_h(free_user))
    assert seen["n"] == 2
    assert "MEET-1" in seen["paths"][0] and "MEET-2" in seen["paths"][1]


# --- failure handling ---

def test_an_upstream_error_is_NOT_cached(client, free_user, monkeypatch):
    """The worst possible interaction with a day-stable key. Caching a CQ
    blip would pin it to that user for a whole UTC day, turning a transient
    failure into an outage nobody can clear."""
    seen = _capture(monkeypatch, body={"detail": "boom"}, status=503)
    first = client.get("/v1/memory/woven", headers=_h(free_user))
    assert first.status_code >= 400
    second = client.get("/v1/memory/woven", headers=_h(free_user))
    assert second.status_code >= 400
    assert seen["n"] == 2, "an error response was cached and replayed"


def test_a_users_digest_is_not_served_to_another_user(
    client, free_user, pro_user, monkeypatch
):
    """The cache key is per user. A shared entry here would be a
    cross-user memory leak, which is the one failure this whole area has
    already had once."""
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven", headers=_h(free_user))
    client.get("/v1/memory/woven", headers=_h(pro_user))
    assert seen["n"] == 2, "two users shared one digest"


# --- project_known: "wrong project" is not "quiet week" ---

def test_project_known_survives_the_hop(client, free_user, monkeypatch):
    """CQ's answer to a question I had framed badly.

    I asked whether to tolerate a project NAME or reject a non-id. Both miss
    the defect: "no such project" and "this project had a quiet week"
    produce an IDENTICAL empty response, and only one of them is a bug.
    Rejecting non-ids leaves the real-but-stale id case returning the same
    silent empty.

    `project_known` false plus an empty `patches` means WRONG PROJECT, and
    the client can say so instead of rendering an empty grid. GP must not
    flatten it.
    """
    _capture(monkeypatch, body={**_digest(0), "project_known": False})
    b = client.get("/v1/memory/woven?project_id=NOPE",
                   headers=_h(free_user)).json()
    assert b["project_known"] is False
    assert b["patches"] == []


def test_a_quiet_week_is_distinguishable_from_a_wrong_project(
    client, free_user, monkeypatch
):
    """The pair, asserted together, because the whole point is that these
    two are no longer the same observable."""
    _capture(monkeypatch, body={**_digest(0), "project_known": True})
    quiet = client.get("/v1/memory/woven?project_id=REAL",
                       headers=_h(free_user)).json()
    woven_cache.clear()
    _capture(monkeypatch, body={**_digest(0), "project_known": False})
    wrong = client.get("/v1/memory/woven?project_id=NOPE",
                       headers=_h(free_user)).json()

    assert quiet["patches"] == wrong["patches"] == []
    assert quiet["project_known"] is not wrong["project_known"], (
        "an empty quilt for a real project reads identically to one for a "
        "project that does not exist"
    )


def test_a_degraded_project_check_is_served_but_NOT_cached(
    client, free_user, monkeypatch
):
    """CQ returns project_known NULL when their existence check failed, so
    an error can never accuse a real project of not existing. That makes
    null-with-a-filter the same kind of answer as a 5xx: honest, and not
    something to pin for a UTC day.

    Caching it would turn one transient blip into a whole day of "we could
    not tell", which is the same anti-pattern as caching an error and
    interacts especially badly with a key that only rolls at midnight.
    """
    seen = _capture(monkeypatch, body={**_digest(2), "project_known": None})
    first = client.get("/v1/memory/woven?project_id=P1", headers=_h(free_user))
    second = client.get("/v1/memory/woven?project_id=P1", headers=_h(free_user))

    assert first.status_code == 200 and second.status_code == 200
    assert len(first.json()["patches"]) == 2, "the user still gets their tiles"
    assert seen["n"] == 2, "a degraded answer was cached day-stable"
    assert first.json()["_freshness"]["cached"] is False


def test_a_null_project_known_with_NO_filter_is_normal_and_cached(
    client, free_user, monkeypatch
):
    """null means "nothing to check" when no project was requested. Treating
    that as degraded would make the unfiltered home digest uncacheable,
    which is the common path."""
    seen = _capture(monkeypatch, body={**_digest(), "project_known": None})
    client.get("/v1/memory/woven", headers=_h(free_user))
    client.get("/v1/memory/woven", headers=_h(free_user))
    assert seen["n"] == 1, "the unfiltered digest stopped caching"
