import time

from fastapi import APIRouter, Request

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
