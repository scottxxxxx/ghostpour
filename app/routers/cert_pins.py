"""Public cert pin manifest serving endpoint.

`GET /v1/config/cert-pins` returns the latest signed pin manifest as JSON
so iOS can verify the signature against its baked-in public key and
adopt the resulting pins.

This route is intentionally NOT part of the remote-config hydration path
that serves `protected-prompts` and friends. The proposal SS sent us
called this out as a circularity concern: the pin manifest cannot ride
on a channel that is itself protected by the pins. Trust comes from the
signature verification (Ed25519 against the baked-in public key), not
from the transport, so the fetch goes over normal HTTPS with no pin
check and an attacker mangling the payload still cannot forge the
signature.

Bootstrap: iOS ships with an initial pin set baked in. If this endpoint
is unreachable or returns 404 (no manifest has been published yet),
iOS falls back to the bootstrap pins.
"""

from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.database import get_db
from app.services.cert_pin_signing import latest_manifest

logger = logging.getLogger("ghostpour.cert_pins")

router = APIRouter()


@router.get("/config/cert-pins")
async def get_cert_pins(
    request: Request,
    response: Response,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Return the most-recent signed cert pin manifest.

    Returns 404 when no manifest has ever been published — iOS treats
    that as "use the bootstrap pins baked into the app." That's the
    correct behavior on a fresh deployment before the first publish.
    """
    manifest = await latest_manifest(db)
    if manifest is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "no_manifest",
                "message": "No cert pin manifest has been published yet.",
            },
        )
    # Short max-age — iOS refreshes regularly. Public + immutable for the
    # life of the version since version is monotonic and signed.
    response.headers["Cache-Control"] = "public, max-age=300"
    return manifest
