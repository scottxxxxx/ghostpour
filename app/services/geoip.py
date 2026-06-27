"""IP -> coarse geography (country + region) from a local MaxMind-format DB.

Provider-agnostic: reads any .mmdb via the maxminddb reader. We ship the
sapics/ip-location-db `dbip-city` database (pulled from GitHub Releases, not npm
— the npm/main builds carry a data-bug notice; the corrected data is in Releases
/ main-test). City is intentionally NOT stored (granularity: country + region).

Graceful: if the DB file is absent or a lookup fails, returns None — geo stays
null and nothing breaks. The reader is opened lazily and cached.

Privacy: the raw IP is looked up at ingestion and only the derived country +
region are kept. The raw IP is never stored (it's hashed separately).

Attribution: the dbip-city data is CC BY 4.0; surface ATTRIBUTION wherever geo
is displayed (dashboard + privacy note).
"""

import logging
import os
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)

ATTRIBUTION = "IP geolocation by DB-IP (https://db-ip.com), CC BY 4.0"

_reader = None
_reader_loaded = False
_lock = threading.Lock()


def _get_reader():
    """Open + cache the .mmdb reader. None when the DB file is absent/unreadable."""
    global _reader, _reader_loaded
    if _reader_loaded:
        return _reader
    with _lock:
        if _reader_loaded:
            return _reader
        _reader_loaded = True
        path = get_settings().geoip_db_path
        if not path or not os.path.isfile(path):
            logger.warning("geoip db not present (%s) — geo lookups disabled until it's installed", path)
            return None
        try:
            import maxminddb
            _reader = maxminddb.open_database(path)
            logger.info("geoip db loaded: %s", path)
        except Exception as e:  # noqa: BLE001 - never let geo break ingestion
            logger.warning("geoip db open failed (%s): %s", path, e)
            _reader = None
        return _reader


def reset_cache() -> None:
    """Drop the cached reader (e.g. after the DB file is refreshed). Tests use this."""
    global _reader, _reader_loaded
    with _lock:
        try:
            if _reader is not None:
                _reader.close()
        except Exception:  # noqa: BLE001
            pass
        _reader = None
        _reader_loaded = False


def lookup(ip: str | None) -> dict | None:
    """Return {'country': 'US', 'region': 'CA'} for an IP, or None.

    country = ISO 3166-1 alpha-2; region = the first subdivision's ISO code
    (falling back to its English name). City is deliberately dropped.
    """
    if not ip:
        return None
    reader = _get_reader()
    if reader is None:
        return None
    try:
        rec = reader.get(ip)
    except Exception:  # noqa: BLE001 - bad IP / reader error -> no geo
        return None
    if not isinstance(rec, dict):
        return None
    country = (rec.get("country") or {}).get("iso_code")
    region = None
    subs = rec.get("subdivisions") or []
    if subs and isinstance(subs[0], dict):
        region = subs[0].get("iso_code") or (subs[0].get("names") or {}).get("en")
    if not country and not region:
        return None
    return {"country": country, "region": region}
