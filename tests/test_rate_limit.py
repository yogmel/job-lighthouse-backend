"""BE-049: in-memory sliding-window rate limit. No clock waits."""

import pytest

from job_lighthouse_backend.common.settings import Settings, SettingsError
from job_lighthouse_backend.companies.rate_limit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_limit_then_says_when():
    clock = Clock()
    limiter = RateLimiter(2, window_seconds=60, clock=clock)
    assert limiter.hit("a") is None
    clock.now += 10
    assert limiter.hit("a") is None
    clock.now += 5
    # The first hit leaves the window 45s from now.
    assert limiter.hit("a") == 45


def test_window_slides():
    clock = Clock()
    limiter = RateLimiter(1, window_seconds=60, clock=clock)
    assert limiter.hit("a") is None
    clock.now += 59
    assert limiter.hit("a") == 1
    clock.now += 1
    assert limiter.hit("a") is None


def test_rejected_hits_do_not_count():
    clock = Clock()
    limiter = RateLimiter(1, window_seconds=60, clock=clock)
    limiter.hit("a")
    for _ in range(5):
        clock.now += 10
        assert limiter.hit("a") is not None
    clock.now += 10
    assert limiter.hit("a") is None


def test_keys_are_independent_and_stale_keys_dropped():
    clock = Clock()
    limiter = RateLimiter(1, window_seconds=60, clock=clock)
    assert limiter.hit("a") is None
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None
    clock.now += 61
    assert limiter.hit("c") is None
    assert set(limiter._hits) == {"c"}


def test_limit_setting_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.delenv("DETECT_LIMIT_PER_HOUR", raising=False)
    assert Settings.from_env().detect_limit_per_hour == 20


def test_limit_setting_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("DETECT_LIMIT_PER_HOUR", "5")
    assert Settings.from_env().detect_limit_per_hour == 5


@pytest.mark.parametrize(("raw", "match"), [("x", "an integer"), ("0", "positive")])
def test_bad_limit_setting(monkeypatch, raw, match):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("DETECT_LIMIT_PER_HOUR", raw)
    with pytest.raises(SettingsError, match=f"DETECT_LIMIT_PER_HOUR must be {match}"):
        Settings.from_env()
