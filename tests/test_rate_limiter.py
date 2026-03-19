from app.services.rate_limiter import RateLimiter


def test_allows_within_limit():
    limiter = RateLimiter()
    for _ in range(5):
        allowed, _ = limiter.check("user1", rpm_limit=5)
        assert allowed


def test_blocks_at_limit():
    limiter = RateLimiter()
    for _ in range(5):
        limiter.check("user1", rpm_limit=5)
    allowed, retry_after = limiter.check("user1", rpm_limit=5)
    assert not allowed
    assert retry_after > 0


def test_separate_users():
    limiter = RateLimiter()
    for _ in range(5):
        limiter.check("user1", rpm_limit=5)
    # user2 should still be allowed
    allowed, _ = limiter.check("user2", rpm_limit=5)
    assert allowed


def test_unlimited_rate():
    limiter = RateLimiter()
    for _ in range(100):
        allowed, _ = limiter.check("user1", rpm_limit=1000)
        assert allowed
