"""Periodic sync of Apple device identifier → marketing name mapping.

Source: adamawolf's gist
(https://gist.githubusercontent.com/adamawolf/3048717/raw/Apple_mobile_device_types.txt),
which is the de facto canonical community-maintained list. Public
domain, updated within days of every Apple release.

Flow:
1. On startup, try to load the previously-synced JSON cache from
   `data/device_models_synced.json`.
2. Spawn a background task that fetches the gist, parses it, and
   refreshes the cache on disk + the in-memory dict.
3. `to_marketing_name()` (in app/services/device_models.py) consults
   the synced cache first, falls back to the hand-curated static
   table on miss. The static table thus acts as a safety net during
   the brief window between deploy and first sync, and for the
   inevitable edge cases the upstream gist hasn't picked up yet.

Cadence: weekly. Apple ships new identifiers a few times per year, so
weekly is generous headroom without hammering the gist.

Fail-soft everywhere: if the gist is unreachable, parsing fails, or
the cache file can't be written, we log a warning and keep serving
from whatever data we already have. The dashboard never breaks
because a community gist had a bad day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger("ghostpour.device_models_sync")

_GIST_URL = (
    "https://gist.githubusercontent.com/adamawolf/3048717/raw/"
    "Apple_mobile_device_types.txt"
)
_SYNC_INTERVAL_SECONDS = 7 * 24 * 60 * 60  # weekly
_FETCH_TIMEOUT_SECS = 30.0

# Line shape in the gist: "iPhone17,3 : iPhone 16"
_LINE_RE = re.compile(r"^\s*([A-Za-z0-9,]+)\s*:\s*(.+?)\s*$")

# Module-level cache populated on startup and refreshed by the daemon.
# Reads are atomic (single dict swap on update), no lock needed.
_synced: dict[str, str] = {}


def get_synced_mapping() -> dict[str, str]:
    """Snapshot of the most recent synced mapping. Returns the empty
    dict when no sync has happened yet."""
    return _synced


def lookup(raw: str) -> str | None:
    """Marketing name from the synced map only. None on miss. Static
    fallback happens in app/services/device_models.to_marketing_name."""
    return _synced.get(raw)


# --- File cache --------------------------------------------------------


def _cache_path() -> Path:
    # Sibling to the SQLite DB so it follows the same data volume.
    return Path("data") / "device_models_synced.json"


def load_cached_from_disk() -> dict[str, str]:
    """Populate the in-memory mapping from a prior sync's JSON cache,
    if one exists. Called once at startup."""
    global _synced
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            _synced = {str(k): str(v) for k, v in data.items()}
            logger.info("device_models_sync loaded cached map entries=%d", len(_synced))
            return _synced
    except Exception as e:
        logger.warning("device_models_sync cache file unreadable: %s", e)
    return {}


def _write_cache(mapping: dict[str, str]) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(mapping, indent=2, sort_keys=True))
    except Exception as e:
        logger.warning("device_models_sync cache write failed: %s", e)


# --- Fetch + parse ----------------------------------------------------


def parse_gist(text: str) -> dict[str, str]:
    """Parse the raw gist text into {identifier: marketing_name}.

    The gist's format is one entry per line as `identifier : name`.
    Comment lines and blanks are ignored. Names are kept as the
    upstream presents them — we don't try to normalize quotes or
    re-case anything.
    """
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        identifier, name = m.group(1), m.group(2)
        # Skip the AppleTV / HomePod / Watch / iPod section headers if any
        # accidentally match — we only care about iPhone/iPad/simulator.
        if not (
            identifier.startswith("iPhone")
            or identifier.startswith("iPad")
            or identifier.startswith("iPod")
            or identifier in ("x86_64", "arm64", "i386")
        ):
            continue
        out[identifier] = name
    return out


async def _fetch_gist_text() -> str:
    """Wrapped so tests can monkeypatch this with a fake text return
    without having to mock httpx's async context manager machinery."""
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECS) as client:
        resp = await client.get(_GIST_URL)
        resp.raise_for_status()
        return resp.text


async def fetch_and_refresh() -> tuple[int, str]:
    """One sync attempt. Returns (entry_count, detail_message). Updates
    in-memory `_synced` and writes the JSON cache on success."""
    global _synced
    try:
        text = await _fetch_gist_text()
        mapping = parse_gist(text)
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return 0, f"fetch failed: {e}"
    except Exception as e:
        return 0, f"unexpected: {e}"

    if not mapping:
        return 0, "parsed zero entries; gist format may have changed"

    _synced = mapping
    _write_cache(mapping)
    return len(mapping), f"refreshed {len(mapping)} entries"


# --- Daemon -----------------------------------------------------------


async def run_daemon(app) -> None:
    """Lifespan-spawned weekly refresher. First tick is delayed by 30
    seconds so startup logs don't get tangled with the fetch, then
    every `_SYNC_INTERVAL_SECONDS`. Fail-soft per the module docstring."""
    await asyncio.sleep(30.0)
    while True:
        try:
            count, detail = await fetch_and_refresh()
            if count > 0:
                logger.info("device_models_sync ok %s", detail)
            else:
                logger.warning("device_models_sync skipped %s", detail)
        except Exception as e:
            logger.warning("device_models_sync tick crashed: %s", e)
        try:
            await asyncio.sleep(_SYNC_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return
