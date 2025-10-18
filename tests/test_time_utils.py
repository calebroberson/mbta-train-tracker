from mbta_tracker import iso_to_local_str
import pytz
from datetime import datetime, timezone

def test_iso_to_local_str_converts_correctly():
    utc_time = datetime(2025, 10, 18, 15, 0, 0, tzinfo=timezone.utc).isoformat()
    local_str = iso_to_local_str(utc_time)
    assert "AM" in local_str or "PM" in local_str
    # Should not crash on valid ISO
    assert isinstance(local_str, str)

def test_iso_to_local_str_handles_none():
    assert iso_to_local_str(None) == ""
