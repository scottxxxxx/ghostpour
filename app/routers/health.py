import time

from fastapi import APIRouter, Request
from fastapi.responses import ORJSONResponse, JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    uptime = time.monotonic() - request.app.state.start_time
    pricing = request.app.state.pricing
    return {
        "status": "ok",
        "version": "0.1.0",
        "uptime_seconds": int(uptime),
        "pricing": {
            "loaded": pricing.is_loaded,
            "model_count": pricing.model_count,
            "source": pricing.source_url,
        },
    }


@router.get("/v1/pricing")
async def pricing(request: Request):
    """Serve the cached pricing data.

    iOS app can use this as a fallback when the primary source
    (e.g., LiteLLM GitHub) is unreachable.

    Returns the full model pricing JSON in the same format as
    LiteLLM's model_prices_and_context_window.json.
    """
    pricing_service = request.app.state.pricing
    if not pricing_service.is_loaded:
        return JSONResponse(
            status_code=503,
            content={"error": "Pricing data not yet loaded"},
        )
    return JSONResponse(
        content=pricing_service._prices,
        headers={"Cache-Control": "public, max-age=3600"},
    )
