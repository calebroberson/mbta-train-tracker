import requests
import responses as responses_lib
from unittest.mock import patch
from mbta_tracker import fetch_predictions, MBTA_API_BASE


@responses_lib.activate
def test_returns_data_and_included():
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/predictions",
        json={
            "data": [{"id": "pred-1"}],
            "included": [{"id": "trip-1", "type": "trip"}],
        },
        status=200,
    )
    data, included = fetch_predictions("place-pktrm", ["Red"])
    assert data == [{"id": "pred-1"}]
    assert included == [{"id": "trip-1", "type": "trip"}]


@responses_lib.activate
def test_returns_empty_lists_on_api_failure():
    for _ in range(3):
        responses_lib.add(
            responses_lib.GET,
            f"{MBTA_API_BASE}/predictions",
            body=requests.exceptions.ConnectionError("fail"),
        )
    with patch("time.sleep"):
        data, included = fetch_predictions("place-pktrm", ["Red"])
    assert data == []
    assert included == []


@responses_lib.activate
def test_multiple_routes_sent_in_one_request():
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/predictions",
        json={"data": [], "included": []},
        status=200,
    )
    fetch_predictions("place-pktrm", ["Red", "Green-B", "Green-C"])
    assert len(responses_lib.calls) == 1
