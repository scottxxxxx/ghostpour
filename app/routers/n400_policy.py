"""N-400 policy matrix endpoint.

`GET /v1/n400/policy?state=TX&form=N-400` returns the effective matrix for
one jurisdiction, so the client's state picker can show what is available
where without evaluating any policy itself. Per-turn decisions do not come
from here; they ride on the turn as `policy_decision`. This endpoint exists
so both teams can smoke the matrix directly, which is what N-400 asked for.

No auth, for the same reason `GET /v1/app/version` has none: the answer is
public policy information, identical for every caller, and the state picker
runs before sign-in. Nothing user-keyed is read or returned.

One asymmetry worth stating, because it looks like an inconsistency and is
not. When the matrix document is missing, a per-turn `evaluate()` fails
CLOSED to BLOCK, while this endpoint returns 503. Different jobs: a turn has
to decide something right now and the safe decision is to stop, whereas a
picker that received a 200 saying "everything is blocked" would render our
outage as our ruling, and might cache it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.services.n400_policy import effective_matrix, policy_document, policy_slug

logger = logging.getLogger("ghostpour.n400_policy")

router = APIRouter()

_DEFAULT_FORM = "N-400"


@router.get("/n400/policy")
async def get_n400_policy(request: Request, state: str | None = None,
                          form: str | None = None):
    return _matrix_for(request, state, form or _DEFAULT_FORM, "n400")


@router.get("/i765/policy")
async def get_i765_policy(request: Request, state: str | None = None,
                          form: str | None = None):
    """I-765 Helper's matrix (2026-10-09). Its own path and its own document
    (`i765/policy-matrix`), never the N-400 one: the client has one URL to
    call, and a wrong URL answers 404 rather than another form's rules."""
    return _matrix_for(request, state, form or "I-765", "i765")


def _matrix_for(request: Request, state: str | None, form: str, app_id: str) -> dict:
    if not state or not state.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "missing_state",
                "message": "state is required, as a USPS two-letter code (TX).",
            },
        )
    # `*` is a wildcard the MATRIX may use to write a default row. A caller
    # asking for it is asking "what applies in no state in particular", which
    # is not a jurisdiction, so it is a request-shape error rather than a
    # lookup that fails closed later.
    if state.strip() == "*":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "wildcard_state_not_a_jurisdiction",
                "message": "state must name a jurisdiction; `*` is matrix syntax.",
            },
        )

    configs = getattr(request.app.state, "remote_configs", None)
    if policy_document(configs, app_id) is None:
        logger.error("%s_policy_unavailable slug=%s", app_id, policy_slug(app_id))
        raise HTTPException(
            status_code=503,
            detail={
                "code": "policy_matrix_unavailable",
                "message": "The policy matrix is not loaded. This is an outage, "
                           "not a policy outcome.",
            },
        )

    return effective_matrix(configs, state, form, app_id)
