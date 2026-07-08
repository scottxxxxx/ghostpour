"""IP -> coarse geography (country + region + city) from local MaxMind-format DBs.

Provider-agnostic: reads any .mmdb via the maxminddb reader. We ship the
sapics/ip-location-db `dbip-city` database (pulled from GitHub Releases, not npm
— the npm/main builds carry a data-bug notice; the corrected data is in the
Releases). sapics splits IPv4 and IPv6 into two files, so we open one reader per
family and route by IP at lookup time (a ':' in the address => IPv6). City is
stored since the #318 targeting approval (2026-07-08): city targeting is on,
guarded by the min-audience floor enforced at campaign authoring and resolve.

Graceful: if a DB file is absent or a lookup fails, returns None — geo stays
null and nothing breaks. Readers are opened lazily and cached per family.

Privacy: the raw IP is looked up at ingestion and only the derived country +
region + city are kept. The raw IP is never stored (it's hashed separately).
No lat/long, no street address.

Attribution: the dbip-city data is CC BY 4.0; surface ATTRIBUTION wherever geo
is displayed (dashboard + privacy note).
"""

import logging
import os
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)

ATTRIBUTION = "IP geolocation by DB-IP (https://db-ip.com), CC BY 4.0"

# family ("v4"/"v6") -> open reader (or absent if not yet loaded / unavailable)
_readers: dict = {}
# families whose load has been attempted (so a missing file isn't retried each call)
_loaded: set = set()
_lock = threading.Lock()


def _family(ip: str) -> str:
    return "v6" if ":" in ip else "v4"


def _db_path(family: str) -> str:
    s = get_settings()
    return s.geoip_db_ipv6_path if family == "v6" else s.geoip_db_path


def _get_reader(family: str):
    """Open + cache the .mmdb reader for an IP family. None when its DB file is
    absent/unreadable."""
    if family in _loaded:
        return _readers.get(family)
    with _lock:
        if family in _loaded:
            return _readers.get(family)
        _loaded.add(family)
        path = _db_path(family)
        if not path or not os.path.isfile(path):
            logger.warning(
                "geoip %s db not present (%s) — %s lookups disabled until it's installed",
                family, path, family,
            )
            return None
        try:
            import maxminddb
            _readers[family] = maxminddb.open_database(path)
            logger.info("geoip %s db loaded: %s", family, path)
        except Exception as e:  # noqa: BLE001 - never let geo break ingestion
            logger.warning("geoip %s db open failed (%s): %s", family, path, e)
        return _readers.get(family)


def reset_cache() -> None:
    """Drop the cached readers (e.g. after the DB files are refreshed). Tests use this."""
    with _lock:
        for r in _readers.values():
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
        _readers.clear()
        _loaded.clear()


def lookup(ip: str | None) -> dict | None:
    """Return {'country': 'US', 'region': '...', 'city': '...'} for an IP, or None.

    country = ISO 3166-1 alpha-2. region = the first-level subdivision; its ISO
    code when available, otherwise its name (the sapics dbip-city build we ship
    only carries the name, e.g. 'California'). city = the locality name —
    extracted since the #318 approval (city targeting on from day one; the
    min-audience floor is enforced downstream at authoring and resolve).
    Still no lat/long, no street, never the raw IP.

    Two record schemas are accepted so the reader stays provider-agnostic:
      - sapics/ip-location-db (flat): {country_code, state1, state2, city, ...}
      - MaxMind GeoIP2-City (nested): {country:{iso_code}, subdivisions:[...]}
    """
    if not ip:
        return None
    reader = _get_reader(_family(ip))
    if reader is None:
        return None
    try:
        rec = reader.get(ip)
    except Exception:  # noqa: BLE001 - bad IP / reader error -> no geo
        return None
    if not isinstance(rec, dict):
        return None
    # country: nested GeoIP2 first, then flat sapics country_code.
    country = (rec.get("country") or {}).get("iso_code") or rec.get("country_code")
    # region: GeoIP2 subdivisions[0] (iso/name), then flat sapics state1.
    region = None
    subs = rec.get("subdivisions") or []
    if subs and isinstance(subs[0], dict):
        region = subs[0].get("iso_code") or (subs[0].get("names") or {}).get("en")
    if not region:
        region = rec.get("state1") or None
    # city: GeoIP2 city.names.en, then flat sapics city.
    city = (rec.get("city") or {}).get("names", {}).get("en") if isinstance(rec.get("city"), dict) else rec.get("city")
    if not country and not region:
        return None
    return {"country": country or None, "region": region or None, "city": city or None}
