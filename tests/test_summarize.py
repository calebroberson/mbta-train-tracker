from mbta_tracker import summarize_prediction

def test_summarize_prediction_with_arrival():
    p = {"attributes": {"arrival_time": "2025-10-18T15:00:00Z", "direction_id": 0}}
    min, headsign, did = summarize_prediction(p)
    assert did == 0
    assert isinstance(min, int)


def test_summarize_prediction_with_missing_times():
    p = {"attributes": {"arrival_time": None, "departure_time": None, "direction_id": 1}}
    min, headsign, did = summarize_prediction(p)
    assert min == None
    assert did == 1
