"""An undeclared patch TYPE must survive GP's hop, both directions.

CQ found ONE patch type in prod that no manifest declares: `artifact`, 5 rows,
all still active, 2026-08-17 through 08-25, with genuinely useful text. (They
first reported five and corrected it themselves within the hour: four came
from a hand-typed TR list in a throwaway script, which is the declared-vs-
actual bug the tool exists to catch, committed by the tool's own author while
building it.) Their question back to GP is the one they cannot answer from
their side, and it is rule 3 exactly:
does GP's middle hop EAT a patch whose type it does not model? If it does,
an undeclared type never reaches a device at all and the orphaning is worse
than CQ could measure alone.

They cannot see it from CQ and SS cannot see it from the device. Only a test
on the middle hop can.

Two directions, because they fail independently:

  REQUEST   a patch created with an unknown type must reach the forward with
            the type intact, not coerced and not stripped.
  RESPONSE  a patch coming BACK with an unknown type must reach the client
            intact. A response model listing known types would eat it on the
            way out, and that failure is invisible from both endpoints for
            the same reason the request-side one was.

⚠ 2026-08-30, AFTER this was written: Scott ruled the memory layer should
not concern itself with artifacts, so CQ's write path is UNCHANGED and the
five rows stay as they are. That does not retire this file. What it tests is
the GENERAL property, that GP does not eat a patch type it does not model,
and `artifact` is just the live value that prompted the question. CQ kept it
reported rather than allowlisted in their own audit for the same reason:
suppressing a known case is how an instrument loses the ability to report an
unknown one.

Note on scope, so nobody reads more into a green run than it earns: the five
`artifact` rows did NOT travel this route. They carry
`source_prompt=meeting_summary, origin_mode=inferred`, so CQ's own extraction
minted them inside CQ and GP's proxy was never in that path. This file proves
GP does not make the orphaning worse and does not prove GP caused it.
"""

import app.routers.cq_proxy as cq

# The live one. Not a made-up canary: this is the type CQ measured landing in
# prod, borrowed by the extraction model from ENTITY_TYPES.
UNDECLARED = "artifact"


def test_request_side_an_unknown_type_reaches_the_forward(
    client, free_user, monkeypatch
):
    """`type` is a bare `str` on PatchCreateRequest, so nothing should
    constrain the VALUE. Proven by running it, because "it is typed str" is a
    claim about the annotation and this is a claim about the request."""
    seen: dict = {}

    async def _capture(m, p, payload=None, *a, **kw):
        seen["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(cq, "_cq_proxy", _capture)

    resp = client.post(
        f"/v1/quilt/{free_user['user_id']}/patches",
        json={"type": UNDECLARED,
              "text": "CIGNA Rebuild Lab: interactive prototype"},
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert resp.status_code < 400, f"{resp.status_code}: {resp.text[:300]}"

    payload = seen.get("payload")
    assert payload is not None, "never reached the forward"
    assert payload.get("type") == UNDECLARED, (
        f"GP altered an undeclared patch type on the way OUT: "
        f"sent {UNDECLARED!r}, forwarded {payload.get('type')!r}. An "
        f"undeclared type would then never reach CQ as sent."
    )
    assert payload.get("text", "").startswith("CIGNA"), "text mangled"


def test_response_side_an_unknown_type_reaches_the_client(
    client, free_user, monkeypatch
):
    """The half CQ explicitly could not see.

    `_cq_proxy` returns a JSONResponse, so an unknown type should pass.

    ⚠ Read what this does and does NOT guard. An earlier version of this
    docstring claimed it would go red if someone added a `response_model` to
    the route. That was FALSE and the sabotage proved it: FastAPI skips
    response_model entirely for a handler returning a Response object, so the
    test stayed green with a response_model bolted on. The claim was written
    from how FastAPI is usually used rather than from how this route works.

    Measured, with a dict return instead: response_model DOES filter then (a
    stray top-level key was dropped). So the protection is not "no response
    model exists", it is "_cq_proxy returns a Response". That mechanism is
    pinned separately below, because it is the thing that can actually change.
    """
    async def _fake(m, p, body=None, *a, **kw):
        from fastapi.responses import JSONResponse
        return JSONResponse(content={"patches": [
            {"id": "p1", "type": UNDECLARED,
             "text": "Single vs. Multi-Agent Decision Document"},
            {"id": "p2", "type": "fact", "text": "a declared one"},
        ]})

    monkeypatch.setattr(cq, "_cq_proxy", _fake)

    resp = client.get(
        f"/v1/quilt/{free_user['user_id']}",
        headers={**free_user["headers"], "X-App-ID": "shouldersurf"},
    )
    assert resp.status_code < 400, f"{resp.status_code}: {resp.text[:300]}"

    types = [p["type"] for p in resp.json().get("patches", [])]
    assert UNDECLARED in types, (
        f"GP ATE an undeclared patch type on the way BACK. Types that "
        f"survived: {types}. This is the to_name shape on the response side: "
        f"invisible from CQ (they sent it) and from the device (it never "
        f"knew), so only this hop can see it."
    )
    assert "fact" in types, "a declared type was lost too; the harness is wrong"


def test_patch_type_is_not_constrained_to_an_enum():
    """A structural backstop for the two behavioural tests above.

    If `type` is ever narrowed to an Enum or Literal, the request test goes
    red anyway. This exists so the REASON is legible in the failure rather
    than being a bare 422, since that is the change most likely to be made
    on purpose by someone who has not read CQ's finding.
    """
    ann = cq.PatchCreateRequest.model_fields["type"].annotation
    assert ann is str, (
        f"PatchCreateRequest.type is {ann!r}, not a bare str. Narrowing it "
        f"means GP starts rejecting types CQ's extraction legitimately mints "
        f"(see `artifact`, 5 live rows). If that is deliberate, GP must be "
        f"told about new types BEFORE they are written, not after."
    )


def test_the_mechanism_that_makes_the_response_side_safe():
    """Pin the property the response-side passthrough actually rests on.

    `_cq_proxy` returning a Response (not a dict) is what makes any
    response_model on any proxy route inert. A refactor to "return the parsed
    body and let FastAPI serialize it" looks tidier, passes every existing
    test, and quietly arms every future response_model to filter CQ's fields
    on the way out. That is the to_name shape on the response side.

    Verified by measurement, not by reading: with a dict return, a stray
    top-level key WAS filtered by a response_model.
    """
    import inspect

    import httpx as _httpx
    from starlette.responses import Response

    # The annotation first, because it makes the failure legible. On its own
    # this would be a claim about a NAME, and a body that stopped returning a
    # Response without touching its own annotation would sail past it. So the
    # real check is below: call it and look at what comes back.
    ret = inspect.signature(cq._cq_proxy).return_annotation
    annotation_ok = isinstance(ret, type) and issubclass(ret, Response)

    class _Resp:
        status_code = 200
        headers: dict = {}

        def json(self):
            return {"patches": [{"id": "p1", "type": UNDECLARED}]}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            return _Resp()

    import asyncio

    from app.config import get_settings

    settings = get_settings()
    if not settings.cq_base_url:
        settings.cq_base_url = "http://cq.invalid"

    async def _noauth(*a, **kw):
        return {}

    orig_client, orig_auth = _httpx.AsyncClient, cq.cq._get_auth_headers
    _httpx.AsyncClient, cq.cq._get_auth_headers = _Client, _noauth
    try:
        out = asyncio.run(cq._cq_proxy("GET", "/v1/quilt/x"))
    finally:
        _httpx.AsyncClient, cq.cq._get_auth_headers = orig_client, orig_auth

    assert isinstance(out, Response), (
        f"_cq_proxy returned {type(out).__name__}, not a Response. If it hands "
        f"FastAPI a plain dict, any response_model on a proxy route starts "
        f"filtering CQ's response, and an undeclared patch type gets eaten on "
        f"the way back with a 200 on every side. "
        f"(return annotation says Response: {annotation_ok})"
    )
