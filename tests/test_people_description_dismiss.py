"""A user must be able to say the inferred description of a person is wrong.

The gap, from Scott's own card: a contact's STATED role had already won the
precedence rule and the card correctly read "Stated, not inferred", so that
half worked. But `entity_descriptions` had no status column and no write
path, and a chat correction operates on `context_patches` and never reaches
it. The inferred series kept rendering under the right title with the wrong
premise, and there was nothing the user could do about it.

CQ's #358 adds the verbs. **A new sub-path 404s from every device until GP's
edge carries it**, so their deploy is blocked on this one and nothing works
until it ships.

What is pinned here is the passthrough, because `note` carries the user's own
words and this is precisely the shape that ate `to_name`: an optional field
on a middle hop, dropped silently, 200 on every side, invisible from both
endpoints.
"""

import pytest

import app.routers.cq_proxy as cq

ENTITY = "ent-123"


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    async def _fake(method, path, body=None, *a, **kw):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = body
        seen["query"] = kw.get("query")
        return {"ok": True}

    monkeypatch.setattr(cq, "_cq_proxy", _fake)
    return seen


def test_the_note_reaches_the_forward(client, free_user, monkeypatch):
    """The `to_name` shape. `note` is the user's own words about a person
    being described wrongly; dropping it turns "correct this to X" into a
    bare "this is inaccurate" with a 200 on every side and no way to tell
    from either end."""
    seen = _capture(monkeypatch)
    resp = client.post(
        f"/v1/people/{free_user['user_id']}/{ENTITY}/descriptions/dismiss",
        json={"note": "Steven is not an immigration attorney.",
              "source": "user_card"},
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert resp.status_code < 400, f"{resp.status_code}: {resp.text[:200]}"
    assert seen["body"]["note"] == "Steven is not an immigration attorney."
    assert seen["body"]["source"] == "user_card"


def test_an_unknown_key_also_survives(client, free_user, monkeypatch):
    """CQ owns the body shape and says `source` is an OPEN vocabulary. So a
    value or a field we have never heard of must ride through, or GP becomes
    the reason their next field does not arrive."""
    seen = _capture(monkeypatch)
    resp = client.post(
        f"/v1/people/{free_user['user_id']}/{ENTITY}/descriptions/dismiss",
        json={"note": "x", "source": "some_future_surface",
              "gp_passthrough_canary": "must-survive"},
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert resp.status_code < 400
    assert seen["body"]["gp_passthrough_canary"] == "must-survive"
    assert seen["body"]["source"] == "some_future_surface"


def test_post_with_NO_body_is_accepted(client, free_user, monkeypatch):
    """No note means "this is inaccurate", which is the common case and the
    one a tap sends. It must not 422."""
    seen = _capture(monkeypatch)
    resp = client.post(
        f"/v1/people/{free_user['user_id']}/{ENTITY}/descriptions/dismiss",
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert resp.status_code < 400, f"{resp.status_code}: {resp.text[:200]}"
    assert seen["body"] is None
    assert seen["method"] == "POST"


def test_delete_with_no_body_is_accepted(client, free_user, monkeypatch):
    """CQ's explicit question: a DELETE carrying no body, on a path whose
    POST takes an optional one. Answered by running it rather than by
    reasoning about how middleboxes usually behave."""
    seen = _capture(monkeypatch)
    resp = client.delete(
        f"/v1/people/{free_user['user_id']}/{ENTITY}/descriptions/dismiss",
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert resp.status_code < 400, f"{resp.status_code}: {resp.text[:200]}"
    assert seen["method"] == "DELETE"
    assert seen["body"] is None


def test_delete_WITH_a_body_is_also_accepted(client, free_user, monkeypatch):
    """Bound optional rather than omitted on purpose. A parameter that
    accepts both nothing and something can never be the reason a call fails,
    whereas binding no body would make a client that sends one for any
    reason (a retry helper, a middlebox, a future field) fail on the shape
    rather than on the intent."""
    seen = _capture(monkeypatch)
    resp = client.request(
        "DELETE",
        f"/v1/people/{free_user['user_id']}/{ENTITY}/descriptions/dismiss",
        json={"source": "user_card"},
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert resp.status_code < 400, f"{resp.status_code}: {resp.text[:200]}"
    assert seen["body"] == {"source": "user_card"}


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_the_path_forwarded_to_cq_is_the_right_one(
    method, client, free_user, monkeypatch
):
    """A proxy that forwards to the wrong sub-path 404s at CQ and looks like
    a CQ bug from here. Pin the shape, including that entity_id lands in the
    path rather than being dropped."""
    seen = _capture(monkeypatch)
    client.request(
        method,
        f"/v1/people/{free_user['user_id']}/{ENTITY}/descriptions/dismiss",
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert seen["path"].endswith(f"/{ENTITY}/descriptions/dismiss")
    assert "/v1/people/" in seen["path"]


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_another_users_person_is_refused(method, client, free_user, monkeypatch):
    """Same guard as every other People route. Checked on BOTH verbs, since
    an undo path that skipped the owner check would be a way to lift another
    user's dismissal."""
    seen = _capture(monkeypatch)
    resp = client.request(
        method,
        f"/v1/people/somebody-else/{ENTITY}/descriptions/dismiss",
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert resp.status_code == 403
    assert seen == {}, "a refused call must never reach the forward"
