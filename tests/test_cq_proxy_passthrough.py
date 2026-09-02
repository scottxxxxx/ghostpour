"""A proxy body model must never silently drop a key it was not told about.

THIRD INSTANCE of one bug, and the reason this file exists rather than another
"add the missing field" commit:

    to_name        silent, found only by a three-way audit
    project_name   LOUD, CQ answered 422 naming the field we ate, 7 calls
    client_id +    SILENT, 200s and correctly rendered rows, found because
    deadline_date  CQ noticed an item that could never become overdue

The class is getting QUIETER as fields get more additive, which is the
direction all three teams ship in, so exposure rises exactly as detectability
falls. A required field fails at the first tap; an optional one produces a
200, a rendered row, and a silence that lasts until somebody wonders why
nothing is ever chased.

Rule 5 in CLAUDE.md describes this bug in advance and did not prevent any of
the three, because a rule with no mechanism is memory. This is the mechanism.

Two things it does deliberately, both at CQ's suggestion:

**It asserts the PROPERTY by running the route, not the config value.** A test
for `model_config["extra"] == "allow"` goes green on a future refactor that
achieves passthrough another way, red on a correct one, and would not catch
the actual failure here at all: it was `model_dump()` that dropped the keys,
and a handler that rebuilds its payload by hand would drop them again with
every model in the file set to allow.

**It enumerates the routes rather than listing them.** A test naming nine
models is a test that passes forever after someone adds a tenth.
"""

from __future__ import annotations

import inspect
from typing import get_args, get_origin

import pytest
from fastapi.params import Depends as DependsParam
from fastapi.routing import APIRoute
from pydantic import BaseModel

import app.routers.cq_proxy as cq
from app.main import app

CANARY = "gp_passthrough_canary"

# Routes whose handler does NOT forward the body to CQ verbatim, so an unknown
# key cannot ride along by design. Named, not skipped silently: a NEW route
# that fails to forward has to be added here on purpose, with a reason.
#
# capture-transcript calls cq.capture() with named arguments and a deliberate
# `passthrough=body.metadata` allowlist. Client extras belong inside that
# `metadata` object, which is what SS's own comment tells its callers to do.
NOT_VERBATIM_FORWARDERS = {"/v1/capture-transcript"}


def _unwrap_optional(annotation):
    """`Model | None` -> `Model`; anything else unchanged.

    See the note at the issubclass check for why this is not cosmetic.
    """
    args = [a for a in get_args(annotation) if a is not type(None)]
    if get_origin(annotation) is not None and len(args) == 1:
        return args[0]
    return annotation


def _body_models():
    """Every cq_proxy route that binds a request BODY to a pydantic model.

    A `Depends(...)` default is what separates the injected UserRecord from an
    actual body, and forgetting that check silently turns this into a test of
    the auth dependency.
    """
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute) or inspect.getmodule(r.endpoint) is not cq:
            continue
        for _name, p in inspect.signature(r.endpoint).parameters.items():
            ann = _unwrap_optional(p.annotation)
            # Unwrap Optional[Model] before the issubclass check. A route
            # binding `body: Model | None = None` annotates a UNION, which is
            # not a type, so a bare issubclass silently skipped it and the
            # route was invisible to this enumeration entirely. That is the
            # blind spot this file exists to close, in this file: a model that
            # can drop keys, on a route the instrument reports as having no
            # model at all.
            #
            # Found by sabotage on 2026-08-31: a typed model bound as
            # Optional on a new route dropped `note` and the inventory stayed
            # green. Every model bound today is non-optional, so it had not
            # bitten yet, which is exactly why it needed finding rather than
            # waiting.
            if (isinstance(ann, type) and issubclass(ann, BaseModel)
                    and not isinstance(p.default, DependsParam)):
                method = sorted(r.methods - {"HEAD", "OPTIONS"})[0]
                out.append(pytest.param(method, r.path, ann,
                                        id=f"{method}:{r.path}"))
    return out


BODY_ROUTES = _body_models()


def _sample(annotation):
    """A value that satisfies a field, so the route reaches its forward
    instead of 422-ing on something unrelated to what we are testing.

    Check the ORIGIN before unwrapping. An earlier version stripped
    Optional[X] and list[X] with the same line, so `list[FromLabel]` reduced
    to FromLabel and produced a string, and reassign-speaker 422'd on a
    harness bug that looked exactly like the defect under test.
    """
    origin = get_origin(annotation)

    if origin is list:
        (elem,) = get_args(annotation) or (None,)
        if isinstance(elem, type) and issubclass(elem, BaseModel):
            return [_minimal(elem)]
        return ["x"]

    if origin is not None and type(None) in get_args(annotation):  # Optional[X]
        inner = next(a for a in get_args(annotation) if a is not type(None))
        return _sample(inner)

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _minimal(annotation)
    if annotation is bool:
        return True
    if annotation is int:
        return 1
    if annotation is dict:
        return {}
    return "x"


def _minimal(model: type[BaseModel]) -> dict:
    return {n: _sample(f.annotation)
            for n, f in model.model_fields.items() if f.is_required()}


def test_there_are_proxy_body_routes_at_all():
    """Guards the parametrised test below against an empty run, which is how
    a discovery bug reads as a green suite."""
    assert len(BODY_ROUTES) >= 8, BODY_ROUTES


@pytest.mark.parametrize("method,path,model", BODY_ROUTES)
def test_an_unmodelled_key_reaches_the_forward(
        method, path, model, client, free_user, monkeypatch):
    if path in NOT_VERBATIM_FORWARDERS:
        pytest.skip(f"{path} forwards via an explicit allowlist, not verbatim")

    seen: dict = {}

    # Captured BY NAME, not by position. The first version took the third
    # positional argument, and when `_cq_proxy` gained a leading `request`
    # parameter (2026-09-02, so the proxy could forward Accept-Language)
    # every argument shifted one place and this read the PATH as the body:
    # eleven tests failed claiming keys had been dropped, when nothing had.
    # A capture that depends on argument order tests the signature as much
    # as the behaviour.
    async def _capture(method, path, body=None, *a, **kw):
        # Bound BY NAME against the real signature rather than by index. An
        # earlier version took a fixed positional slot and broke twice in one
        # session: once when `request` was briefly added as a leading
        # positional parameter (every argument shifted and this read the PATH
        # as the body, failing 26 tests across nine files for a reason that
        # had nothing to do with passthrough), and again when that parameter
        # was made keyword-only and everything shifted back.
        seen["payload"] = kw["body"] if "body" in kw else body
        return {"ok": True}

    monkeypatch.setattr(cq, "_cq_proxy", _capture)

    body = _minimal(model)
    # Some routes validate beyond required-ness, so a generically minimal body
    # would 422 before reaching the forward and the test would fail for a
    # reason that has nothing to do with passthrough.
    if path.endswith("/reassign-speaker"):
        body["to_name"] = "Alex"          # exactly one target is required
    body[CANARY] = "must-survive"

    url = path.replace("{user_id}", free_user["user_id"])
    for token in ("{patch_id}", "{origin_id}", "{meeting_id}",
                  "{origin_type}", "{project_id}", "{entity_id}"):
        url = url.replace(token, "x")

    resp = client.request(method, url, json=body,
                          headers={**free_user["headers"],
                                   "X-App-ID": "shouldersurf"})
    assert resp.status_code < 400, f"{method} {url} -> {resp.status_code}: {resp.text[:200]}"

    payload = seen.get("payload")
    assert payload is not None, f"{path} never reached _cq_proxy"
    assert CANARY in payload, (
        f"{path} DROPPED an unmodelled key. This is the to_name shape: a "
        f"field the client really sent, eaten on the middle hop, invisible "
        f"from both endpoints. Forwarded keys were {sorted(payload)}."
    )


def test_the_two_fields_that_were_being_eaten_are_modelled_by_name():
    """extra="allow" is the floor, not the contract. These two are known and
    should be typed and documented rather than merely tolerated."""
    assert "client_id" in cq.PatchCreateRequest.model_fields
    assert "deadline_date" in cq.PatchCreateRequest.model_fields
    # SS's updatePatch sends this; we modelled `category` and not this, so
    # every patch-type edit was a silent no-op.
    assert "patch_type" in cq.PatchUpdateRequest.model_fields


@pytest.mark.parametrize("raw", [
    "2026-09-01", "2026-9-1", "2026-09-01T00:00:00Z", "next tuesday",
    "", "2026-13-45",
])
def test_deadline_date_is_passed_through_byte_identical(raw):
    """CQ stores and echoes the EXACT string and SS compares it, so any
    normalisation on this hop is a defect (CQ, 2026-08-30).

    Typing this field as `datetime.date` would look like an improvement and
    would quietly break that contract: it would coerce "2026-9-1" to
    "2026-09-01" and reject the malformed ones here, where CQ has deliberately
    different rules per route (dropped with a warning on create, so a user
    does not lose the task they just typed; 422 INVALID_DEADLINE_DATE on
    edit, because the item is already safe and a silent no-op reported as 200
    is worse). Neither of those decisions is ours to pre-empt from the middle.
    """
    created = cq.PatchCreateRequest(type="commitment", text="t",
                                    deadline_date=raw).model_dump()
    updated = cq.PatchUpdateRequest(deadline_date=raw).model_dump()
    assert created["deadline_date"] == raw
    assert updated["deadline_date"] == raw
    assert isinstance(created["deadline_date"], str)


def test_patch_update_keeps_both_spellings():
    """Three-way mismatch (CQ, 2026-08-30): we modelled `category`, SS sends
    `patch_type`, and CQ's PatchUpdate modelled `category` too. Correcting any
    ONE of the three leaves the bug alive, so CQ now accepts both and we must
    keep forwarding both rather than renaming. Renaming to fix a compatibility
    bug is how you get a third name."""
    assert "category" in cq.PatchUpdateRequest.model_fields
    assert "patch_type" in cq.PatchUpdateRequest.model_fields
