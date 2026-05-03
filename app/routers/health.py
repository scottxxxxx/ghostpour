import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()


def _health_payload(request: Request) -> dict:
    uptime = time.monotonic() - request.app.state.start_time
    pricing = request.app.state.pricing
    return {
        "status": "ok",
        "version": "0.4.0",
        "uptime_seconds": int(uptime),
        "pricing": {
            "loaded": pricing.is_loaded,
            "model_count": pricing.model_count,
            "source": pricing.source_url,
        },
    }


@router.get("/health")
async def health(request: Request):
    return _health_payload(request)


# Alias under /v1 because Nginx Proxy Manager (bifrost) was
# health-checking /v1/health and getting 404s — every poll was a
# spurious "container unhealthy" data point and a noisy log line.
# Mirroring the route is cheaper than reconfiguring NPM and lets any
# future caller use whichever path matches their convention.
@router.get("/v1/health")
async def health_v1(request: Request):
    return _health_payload(request)


@router.get("/admin")
async def admin_ui():
    """Serve the admin dashboard."""
    html_path = Path(__file__).parent.parent / "static" / "admin.html"
    return FileResponse(html_path, media_type="text/html")


@router.get("/v1/model-pricing")
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
