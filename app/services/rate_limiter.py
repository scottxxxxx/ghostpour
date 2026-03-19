import time
from collections import defaultdict


class RateLimiter:
    """In-memory sliding window rate limiter. Resets on process restart."""

    def __init__(self):
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, user_id: str, rpm_limit: int) -> tuple[bool, int]:
        """Check if request is allowed.

        Returns (allowed, retry_after_seconds).
        """
        now = time.monotonic()
        window = 60.0

        bucket = self._buckets[user_id]
        self._buckets[user_id] = [t for t in bucket if now - t < window]
        bucket = self._buckets[user_id]

        if len(bucket) >= rpm_limit:
            oldest = bucket[0]
            retry_after = int(window - (now - oldest)) + 1
            return False, retry_after

        bucket.append(now)
        return True, 0
