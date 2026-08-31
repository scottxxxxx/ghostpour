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


# --- the echo: CQ's body must survive byte-identical -----------------------

CQ_REAL_BODY = {
    "total_memories": 2770,
    "meetings_count": 41,
    "since": "2026-03-01",
    "window_days": 7,
    "project_known": None,
    "patches": [
        {"patch_id": "p1", "patch_type": "takeaway",
         "fact": "Target market of 60-67% small firms is ideal.",
         # Written by a model at ingest, NOT truncated from fact. The tiles
         # render this and the meeting list renders fact; truncating fact
         # for a tile reads as a cut-off sentence, which is the most visible
         # thing on the screen.
         "headline": "60-67% small firms is the sweet spot",
         "weight": 3, "span": 3, "height": 118,
         "source_meeting_id": "MEET-1",
         "occurred_at": "2026-08-28T16:11:00Z",
         "stitched_to": [{"patch_id": "p9", "label": "$3M ARR goal"},
                         {"patch_id": "p7", "label": "Go-to-market"}]},
        {"patch_id": "p2", "patch_type": "constraint",
         "fact": "Zero data retention with AI providers.",
         # NULL is a real state, not a bug: the line is REFUSED when it
         # breaks the design rules, and existing patches carry none until a
         # backfill runs. A client that treats null as an error would show a
         # failure for a patch that is working correctly.
         "headline": None,
         "weight": 2, "span": 3, "height": 118,
         "source_meeting_id": "MEET-1",
         "occurred_at": "2026-08-28T16:17:00Z",
         "stitched_to": []},
    ],
    # An ARRAY OF PAIRS. A flattening or re-sorting hop destroys the layout
    # and nothing errors: the quilt just renders wrong.
    "row_pairs": [[0, 1], [2, 3], [4, 5]],
    # An OPEN map. Keys are not a fixed vocabulary and new ones get added,
    # so anything that enumerates known keys silently drops the new ones.
    "dropped": {"no_text": 353, "sensitive_content": 4, "some_future_rule": 1},
}


def test_cqs_whole_body_survives_byte_identical(client, free_user, monkeypatch):
    """The echo CQ asked for, as a test rather than a status code.

    "A 200 has told us the wrong thing twice." So this asserts the body GP
    forwards is byte-identical to what CQ served, not that the call
    succeeded. Compared as a whole document rather than key by key, because
    a per-key check only finds the keys someone thought to list, and the
    failure mode is the key nobody thought of.
    """
    _capture(monkeypatch, body=CQ_REAL_BODY)
    got = client.get("/v1/memory/woven", headers=_h(free_user)).json()

    served = {k: v for k, v in got.items() if k != "_freshness"}
    assert served == CQ_REAL_BODY, "GP altered CQ's body in transit"


def test_row_pairs_keeps_its_nesting_and_order(client, free_user, monkeypatch):
    """Called out by CQ specifically: an array of arrays, where a flattening
    or re-sorting middlebox destroys the layout SILENTLY. Asserted on its
    own because equality above would pass a document that was right for
    other reasons."""
    _capture(monkeypatch, body=CQ_REAL_BODY)
    got = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    assert got["row_pairs"] == [[0, 1], [2, 3], [4, 5]]
    assert all(isinstance(r, list) for r in got["row_pairs"]), "rows flattened"


def test_dropped_keeps_UNKNOWN_reason_keys(client, free_user, monkeypatch):
    """`dropped` looks like debug output and is not.

    It diagnosed a total CQ failure in one run: 353 candidates in, 0 tiles
    out, reading no_text: 353. An empty quilt WITH a reason is a different
    product state from an empty one without, and only one of them is a bug.

    The keys are an OPEN vocabulary, so the thing to pin is that a reason GP
    has never heard of still arrives.
    """
    _capture(monkeypatch, body=CQ_REAL_BODY)
    got = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    assert got["dropped"]["no_text"] == 353
    assert got["dropped"]["some_future_rule"] == 1, (
        "an unrecognised prune reason was dropped; the vocabulary is open "
        "and GP must not enumerate it"
    )


def test_patch_links_survive_as_objects_not_strings(
    client, free_user, monkeypatch
):
    """CQ corrected me on this: the prototype renders flat label strings, but
    the contract is {patch_id, label} because every link must OPEN a patch.
    Serving labels alone loses the tap and would look fine on screen."""
    _capture(monkeypatch, body=CQ_REAL_BODY)
    got = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    link = got["patches"][0]["stitched_to"][0]
    assert isinstance(link, dict)
    assert link["patch_id"] == "p9" and link["label"] == "$3M ARR goal"


def test_a_new_cq_field_needs_no_gp_change(client, free_user, monkeypatch):
    """Why the echo test is byte-identity rather than a key list.

    CQ is adding `headline` and warned it might break a fixture asserting a
    fixed set of patch keys. It does not, and the reason is the design: the
    test asserts GP returns whatever CQ sent, so a field GP has never heard
    of passes by construction rather than by being enumerated.

    That is the property worth having. A key list would have to be updated
    for every CQ field forever, and the update that gets forgotten is
    exactly the field that then goes missing silently.
    """
    invented = {
        **CQ_REAL_BODY,
        "a_field_gp_has_never_heard_of": {"nested": [1, 2, 3]},
    }
    invented["patches"] = [
        {**CQ_REAL_BODY["patches"][0], "some_future_patch_key": "keep me"},
        *CQ_REAL_BODY["patches"][1:],
    ]
    _capture(monkeypatch, body=invented)
    got = client.get("/v1/memory/woven", headers=_h(free_user)).json()

    served = {k: v for k, v in got.items() if k != "_freshness"}
    assert served == invented
    assert served["patches"][0]["some_future_patch_key"] == "keep me"


def test_a_null_headline_is_carried_not_dropped(client, free_user, monkeypatch):
    """Null is a REAL state: the headline is refused when it breaks the
    design's rules, and patches predating the backfill have none. Dropping
    the key would make "refused" and "not yet generated" indistinguishable
    from "this CQ version has no headlines at all"."""
    _capture(monkeypatch, body=CQ_REAL_BODY)
    got = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    assert "headline" in got["patches"][1], "a null headline key was dropped"
    assert got["patches"][1]["headline"] is None
    assert got["patches"][0]["headline"] == "60-67% small firms is the sweet spot"


# --- _freshness: ONE shape, always ------------------------------------------

import datetime as _dt  # noqa: E402


def _freshness_of(resp) -> dict:
    return resp.json()["_freshness"]


def test_freshness_has_the_same_keys_on_every_path(
    client, free_user, monkeypatch
):
    """One shape, three paths.

    The first version built the degraded envelope inline with an extra
    `cached` key the normal path lacked. Two shapes for one field, and the
    rarer one is the one a client gets wrong, which is the same reasoning as
    logging a detector's clean case.
    """
    expected = {"as_of", "stale", "cached"}

    _capture(monkeypatch)
    fresh = _freshness_of(client.get("/v1/memory/woven", headers=_h(free_user)))
    assert set(fresh) == expected, f"fresh path: {sorted(fresh)}"

    woven_cache.clear()
    _capture(monkeypatch, body={**_digest(2), "project_known": None})
    degraded = _freshness_of(
        client.get("/v1/memory/woven?project_id=P1", headers=_h(free_user)))
    assert set(degraded) == expected, f"degraded path: {sorted(degraded)}"
    assert degraded["cached"] is False


def test_as_of_is_never_null_and_is_a_bare_DAY(client, free_user, monkeypatch):
    """SS parses this as a day with a formatter pinned to en_US_POSIX/UTC,
    because it is a machine day rather than a displayed one.

    A null would throw their decoder, the fetch would return nil, and the
    screen would fall back to the local builder, which on a device is
    INDISTINGUISHABLE from the route being dark. That is the lean-patch
    defect they just fixed, and the degraded path had it in my field.

    A time component would be worse than useless: it would invent precision
    GP never has and drift by the reader's zone.
    """
    for url, body in (
        ("/v1/memory/woven", None),
        ("/v1/memory/woven?project_id=P1", {**_digest(2), "project_known": None}),
        ("/v1/memory/meetings/MEET-1/woven", None),
    ):
        woven_cache.clear()
        _capture(monkeypatch, body=body)
        got = _freshness_of(client.get(url, headers=_h(free_user)))
        assert got["as_of"] is not None, f"{url} sent a null day"
        # Parses as a bare date, and ONLY as a bare date.
        _dt.date.fromisoformat(got["as_of"])
        assert "T" not in got["as_of"] and ":" not in got["as_of"], (
            f"{url} sent {got['as_of']!r}; a time invents precision GP does "
            f"not have and drifts by the reader's zone"
        )


# --- CQ's REAL shapes, generated by running their code ----------------------
#
# Not written by hand. Every hand-built fixture in this session hid a defect:
# OVERDUE without its parentheses, then bracketed detail groups omitted, then
# the home shape reused for the seam. CQ generated these from the live path.

# The leanest HOME patch. `headline` is null by DEFAULT, not exceptionally:
# the lane writes forward only, so every patch that already exists has null
# until a backfill runs. Null headlines are the first real contact.
LEAN_HOME_PATCH = {
    "patch_id": "1111-aaaa", "patch_type": "commitment",
    "fact": "Send the revised scope by Thursday",
    "headline": None,
    "weight": 3, "span": 6, "height": 118,
    "source_meeting_id": "2222-bbbb",
    "occurred_at": None,          # genuinely nullable
    # The UNKNOWN-KEY PROBE, deliberately synthetic.
    #
    # This slot held `_salience`, a real CQ-internal field, until CQ #364
    # pulled it from the wire. Nothing about GP broke, but the fixture then
    # described a shape CQ no longer sends, and the obvious repair (delete
    # the key, delete the assert) deletes the PROPERTY along with it: that
    # GP forwards keys it does not understand. So the carrier is now a name
    # CQ will never define. It cannot drift when their schema moves, and it
    # cannot be mistaken for a stale CQ field and tidied away by the next
    # reader, which is exactly how `_salience` came to be sitting here.
    #
    # The value is NESTED on purpose. Flattening is one of the passthrough
    # failures this suite exists to catch and a scalar cannot detect it.
    "_gp_unknown_key_probe": {"nested": ["a", 1, None]},
    "stitched_to": [],
}

# ⚠ The SEAM patch is LEANER: no weight, span or height AT ALL, because the
# seam is capture order with nothing to tile. A decoder that requires those
# three throws on the seam while the home route succeeds, which reads as
# "one route is broken" rather than "two shapes exist".
LEAN_SEAM_PATCH = {
    "patch_id": "3333-cccc", "patch_type": "takeaway",
    "fact": "Privacy is the differentiator",
    "headline": None,
    "source_meeting_id": "2222-bbbb",
    "occurred_at": None,
    "stitched_to": None,          # null = CQ could not compute the links
}

LEAN_HOME_BODY = {
    "total_memories": 2770, "meetings_count": 41, "since": "2026-03-01",
    "window_days": 7, "project_known": None,
    "patches": [LEAN_HOME_PATCH],
    "row_pairs": [[0]],
    # ALWAYS present. A POPULATED map is the NORMAL case, not an error:
    # a healthy six-tile digest for Scott reads
    # {'person_type_not_rendered': 21, 'episode_without_origin': 12}, which
    # are correct exclusions. An empty-state sentence keyed off "dropped is
    # non-empty" would fire on every healthy response.
    "dropped": {"person_type_not_rendered": 21, "episode_without_origin": 12},
}

LEAN_SEAM_BODY = {
    "meeting_id": "2222-bbbb",
    "patches": [LEAN_SEAM_PATCH],
    "dropped": {},                # {} is the "nothing pruned" signal
}


# --- paging: CQ moved to limit 1..60 plus a new `offset` (2026-08-31) ------
#
# Rule 3: a response-side test cannot see a request-side hole. Every
# assertion here reads the OUTBOUND query GP built, because that is the only
# place a dropped or clamped param is visible. Both ends look healthy: SS
# gets a 200 with correct-looking tiles, CQ never sees the value that was
# lost, so the whole defect lives on this hop.

def test_offset_is_forwarded_to_cq(client, free_user, monkeypatch):
    """The dark half. Without this, every page request returns page one."""
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven?window=7d&limit=6&offset=12",
               headers=_h(free_user))
    assert "offset=12" in seen["queries"][0], seen["queries"][0]


def test_offset_reaches_the_CACHE_KEY_not_just_the_wire(
    client, free_user, monkeypatch
):
    """The half that forwarding alone does not fix.

    If offset rides the wire but not the key, page two asks CQ correctly and
    is then served page one out of GP's cache. A test that only checked the
    outbound query would pass while the user still cannot scroll, so this
    asserts CQ was consulted a SECOND time for a second offset.
    """
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven?window=7d&limit=6&offset=0",
               headers=_h(free_user))
    client.get("/v1/memory/woven?window=7d&limit=6&offset=6",
               headers=_h(free_user))
    assert seen["n"] == 2, (
        f"offset is not in the cache key: {seen['n']} call(s) to CQ for two "
        f"different pages, so the second page was served from page one's entry"
    )
    assert "offset=6" in seen["queries"][1], seen["queries"][1]


def test_a_limit_of_30_is_not_silently_clamped_to_6(
    client, free_user, monkeypatch
):
    """CQ's range is 1..60. A ceiling of 6 here did not reject a larger ask,
    it quietly served the wrong page size under a 200."""
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven?window=7d&limit=30", headers=_h(free_user))
    assert "limit=30" in seen["queries"][0], seen["queries"][0]


def test_the_ceiling_is_CQ_s_60_and_still_bounded(
    client, free_user, monkeypatch
):
    """Track the partner's range, but do not become unbounded doing it."""
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven?window=7d&limit=600", headers=_h(free_user))
    assert "limit=60" in seen["queries"][0], seen["queries"][0]


def test_a_garbage_offset_pages_to_the_start_rather_than_422(
    client, free_user, monkeypatch
):
    """Same leniency argument as `limit`: a typo must not cost the tab.
    A plain `int` annotation would 422 before CQ's tolerance ever ran."""
    seen = _capture(monkeypatch)
    r = client.get("/v1/memory/woven?window=7d&offset=banana",
                   headers=_h(free_user))
    assert r.status_code == 200, r.status_code
    assert "offset=0" in seen["queries"][0], seen["queries"][0]


def test_a_negative_offset_clamps_to_CQ_s_floor(
    client, free_user, monkeypatch
):
    seen = _capture(monkeypatch)
    client.get("/v1/memory/woven?window=7d&offset=-5", headers=_h(free_user))
    assert "offset=0" in seen["queries"][0], seen["queries"][0]


def test_the_lean_home_shape_survives(client, free_user, monkeypatch):
    """CQ's real leanest home patch, not an idealised one."""
    _capture(monkeypatch, body=LEAN_HOME_BODY)
    got = client.get("/v1/memory/woven", headers=_h(free_user)).json()
    p0 = got["patches"][0]

    # The NAMED asserts run BEFORE the byte-identity one on purpose. Whole
    # body equality catches every mutation below first, so any assert placed
    # after it can never fire and its message can never print: measured, not
    # assumed, by stripping underscore keys in the handler and watching the
    # equality fail while these three sat unreached. Specific first, catch
    # all last.
    assert "headline" in p0 and p0["headline"] is None
    assert "occurred_at" in p0 and p0["occurred_at"] is None
    assert p0.get("_gp_unknown_key_probe") == {"nested": ["a", 1, None]}, (
        "GP dropped or reshaped a key it does not understand"
    )
    assert {k: v for k, v in got.items() if k != "_freshness"} == LEAN_HOME_BODY


def test_the_SEAM_shape_has_no_layout_fields_and_still_survives(
    client, free_user, monkeypatch
):
    """The one that would have broken a route.

    The seam carries no weight, span or height because it is capture order
    with nothing to tile. GP must not require them, invent them, or notice
    they are missing. A hop that normalised the two shapes together would
    make the seam look like a home digest with holes in it.
    """
    _capture(monkeypatch, body=LEAN_SEAM_BODY)
    got = client.get("/v1/memory/meetings/2222-bbbb/woven",
                     headers=_h(free_user)).json()
    assert {k: v for k, v in got.items() if k != "_freshness"} == LEAN_SEAM_BODY
    p0 = got["patches"][0]
    for layout in ("weight", "span", "height"):
        assert layout not in p0, f"GP invented {layout} on a seam patch"


def test_a_null_stitched_to_is_carried_as_null(client, free_user, monkeypatch):
    """Three states, not two. A list means these are the links, [] means
    none, and NULL means CQ could not compute them because their link query
    failed. Collapsing null to [] would turn a real failure into a confident
    'this patch has no connections', which is the same shape as every other
    absence we have chased tonight."""
    _capture(monkeypatch, body=LEAN_SEAM_BODY)
    got = client.get("/v1/memory/meetings/2222-bbbb/woven",
                     headers=_h(free_user)).json()
    p0 = got["patches"][0]
    assert "stitched_to" in p0, "the key was dropped"
    assert p0["stitched_to"] is None, "null was collapsed to a list"


def test_an_empty_dropped_map_is_carried_not_omitted(
    client, free_user, monkeypatch
):
    """`{}` is the 'nothing pruned' signal and is never absent. If GP
    omitted an empty map, SS would have to handle two encodings of one
    state, and the rare one is the one that gets it wrong."""
    _capture(monkeypatch, body=LEAN_SEAM_BODY)
    got = client.get("/v1/memory/meetings/2222-bbbb/woven",
                     headers=_h(free_user)).json()
    assert "dropped" in got and got["dropped"] == {}
