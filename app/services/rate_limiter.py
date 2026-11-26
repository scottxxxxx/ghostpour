import time
from collections import defaultdict


class RateLimiter:
    """In-memory sliding window rate limiter. Resets on process restart."""

    def __init__(self):
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, user_id: str, rpm_limit: int, window: float = 60.0) -> tuple[bool, int]:
        """Check if request is allowed.

        `rpm_limit` is the number of requests allowed per `window` seconds;
        the window defaults to a minute, which every caller but one uses.
        Crash intake (app/routers/diagnostics.py) counts per hour. A key
        must always be checked with the same window, so callers give each
        window its own key prefix.

        Returns (allowed, retry_after_seconds).
        """
        now = time.monotonic()

        bucket = self._buckets[user_id]
        self._buckets[user_id] = [t for t in bucket if now - t < window]
        bucket = self._buckets[user_id]

        if len(bucket) >= rpm_limit:
            oldest = bucket[0]
            retry_after = int(window - (now - oldest)) + 1
            return False, retry_after

        bucket.append(now)
        return True, 0

    def peek(self, user_id: str, rpm_limit: int, window: float = 60.0) -> tuple[bool, int]:
        """What `check` would answer, without taking a slot.

        For a caller holding several limits at once (Help Ask: per device
        per hour, per device per day, per IP per hour): it peeks at all of
        them and only then takes a slot in each, so a request one limit
        refuses is not counted against the others.
        """
        now = time.monotonic()
        live = [t for t in self._buckets.get(user_id, ()) if now - t < window]
        if len(live) >= rpm_limit:
            return False, (int(window - (now - live[0])) + 1) if live else int(window)
        return True, 0
