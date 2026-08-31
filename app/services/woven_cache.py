"""Caching for the Woven memory digest.

The spec asks for a 15 minute TTL on the home digest. That is the wrong
mechanism for the property it actually wants, which the spec states one line
earlier: **a tile should not vanish between two opens of the tab in the same
day.** A TTL does not deliver that; it guarantees the opposite, that the
digest CAN move mid-day, and when the ranking shifts the mosaic reshuffles
under the user. CQ raised this and they are right.

So the key is DAY-STABLE. Within one UTC day a user asking for the same
window and project gets the same digest, and it rolls once. That is the same
discipline recall already runs under for the upstream prompt cache, so it is
a pattern both teams already maintain rather than a new one.

STALE-WHILE-REVALIDATE on top. A cached digest is served immediately even
when it has rolled, and the refresh runs behind the response. That collapses
the warm-ahead-versus-live question the spec poses: the user always gets
bytes at once, and the "as of <date>" overline the spec defines for the slow
path becomes the NORMAL path rather than the degraded one.

Which leaves exactly one genuinely slow case, the cold one, where there is
nothing stale to serve. Warming is an optimisation for that case and only
that case, and nobody has measured whether it is slow, so nothing here warms
anything. A standing load deserves a number rather than an intuition; the
extraction gate that priced out at $0.84 a month is the local precedent.

In-process and per-worker on purpose. This caches a per-user read that is
cheap to recompute and safe to serve twice, so a shared store would buy
consistency nobody can observe at the cost of a dependency.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Entries older than this are evicted outright rather than served stale. A
# digest from last week is not "as of" anything a user would recognise, and
# serving it would make the overline a lie rather than a caveat.
_MAX_STALE_DAYS = 7

# A cap so a long-lived worker cannot accumulate one entry per user forever.
_MAX_ENTRIES = 2048


@dataclass
class Entry:
    body: dict
    day: date
    stored_at: datetime
    refreshing: bool = False


_CACHE: dict[str, Entry] = {}
# Keys with a refresh already in flight, so N concurrent opens of a rolled
# digest launch ONE fan-out rather than N.
_LOCK_HOLDERS: set[str] = set()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def key(user_id: str, kind: str, *parts: str | None) -> str:
    """Cache key. The DAY is deliberately NOT in it.

    Putting the day in the key would make a roll a cache MISS, which is a
    cold read and a spinner. The day is stored on the entry instead, so a
    roll is a stale HIT: serve immediately, refresh behind.
    """
    return "|".join([kind, user_id, *[p or "" for p in parts]])


def peek(k: str) -> Entry | None:
    e = _CACHE.get(k)
    if e is None:
        return None
    if (_today() - e.day).days > _MAX_STALE_DAYS:
        _CACHE.pop(k, None)
        return None
    return e


def is_fresh(e: Entry) -> bool:
    return e.day == _today()


def store(k: str, body: dict) -> None:
    if len(_CACHE) >= _MAX_ENTRIES and k not in _CACHE:
        # Oldest first. A digest nobody has asked for since yesterday is the
        # cheapest thing to lose.
        oldest = min(_CACHE, key=lambda x: _CACHE[x].stored_at)
        _CACHE.pop(oldest, None)
    _CACHE[k] = Entry(body=body, day=_today(),
                      stored_at=datetime.now(timezone.utc))


def today_iso() -> str:
    """The day a NOT-cached answer is as of.

    Exists so the degraded path and the cached path format the day the same
    way rather than each rolling their own. SS pins their parser to
    en_US_POSIX/UTC precisely because this is a machine day, and two
    formatters is how one of them quietly stops matching.
    """
    return _today().isoformat()


def as_of(e: Entry) -> str:
    """What the client stamps on the overline when serving a stale digest."""
    return e.day.isoformat()


def clear() -> None:
    _CACHE.clear()
    _LOCK_HOLDERS.clear()


async def get_or_refresh(
    k: str, fetch: Callable[[], Awaitable[dict]]
) -> tuple[dict, bool]:
    """Return (body, was_stale).

    Fresh hit  -> serve, no work.
    Stale hit  -> serve IMMEDIATELY, refresh in the background.
    Miss       -> await the fetch, because there is nothing to serve.

    A single refresh runs per key. Without that, N concurrent opens of a
    rolled digest each launch a fan-out, which is a thundering herd aimed at
    CQ at exactly the moment everyone opens the app in the morning.
    """
    cached = peek(k)

    if cached is not None and is_fresh(cached):
        return cached.body, False

    if cached is not None:
        if k not in _LOCK_HOLDERS:
            _LOCK_HOLDERS.add(k)

            async def _refresh() -> None:
                try:
                    body = await fetch()
                    store(k, body)
                except Exception:
                    # A failed refresh must NOT evict: the stale copy is
                    # still the best answer we have, and dropping it would
                    # turn a transient CQ blip into a cold read for everyone.
                    logger.warning("woven_refresh_failed key=%s", k,
                                   exc_info=True)
                finally:
                    _LOCK_HOLDERS.discard(k)

            asyncio.create_task(_refresh())
        return cached.body, True

    body = await fetch()
    store(k, body)
    return body, False
