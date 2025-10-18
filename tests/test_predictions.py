import responses
from mbta_tracker import fetch_predictions, MBTA_API_BASE

@responses.activate
def test_fetch_predictions_parses_data():
    stop_id = "place-pktrm"
    route_id = "Red"
    direction_id = 1

    responses.add(
        responses.GET,
        f"{MBTA_API_BASE}/predictions",
        json={
            "data": [
                {"id": "p1", "attributes": {"arrival_time": "2025-10-18T14:00:00Z", "direction_id": direction_id}},
                {"id": "p2", "attributes": {"departure_time": "2025-10-18T14:05:00Z", "direction_id": direction_id}},
            ]
        },
        status=200,
    )

    data = fetch_predictions(stop_id, route_id, direction_id)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["id"] == "p1"
