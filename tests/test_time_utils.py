from datetime import datetime, timezone
from mbta_tracker import minutes_until


def test_returns_none_for_none():
    assert minutes_until(None) is None


def test_returns_none_for_empty_string():
    assert minutes_until("") is None


def test_returns_none_for_unparsable():
    assert minutes_until("not-a-date") is None


def test_future_returns_floor_minutes():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # 5 min 30 sec in the future → floor to 5
    assert minutes_until("2026-01-01T12:05:30+00:00", now=now) == 5


def test_past_clamps_to_zero():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert minutes_until("2026-01-01T11:55:00+00:00", now=now) == 0


def test_z_suffix_treated_as_utc():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert minutes_until("2026-01-01T12:03:00Z", now=now) == 3


def test_exactly_now_returns_zero():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert minutes_until(now.isoformat(), now=now) == 0


def test_uses_real_now_when_not_provided():
    result = minutes_until("2099-01-01T00:00:00Z")
    assert isinstance(result, int)
    assert result > 0
