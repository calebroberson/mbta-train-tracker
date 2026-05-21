import requests
import responses as responses_lib
from unittest.mock import patch
from mbta_tracker import mbta_get, MBTA_API_BASE


@responses_lib.activate
def test_success_returns_parsed_json():
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/stops",
        json={"data": [{"id": "place-pktrm"}]},
        status=200,
    )
    assert mbta_get("/stops", {}) == {"data": [{"id": "place-pktrm"}]}


@responses_lib.activate
def test_all_retries_exhausted_returns_empty():
    for _ in range(3):
        responses_lib.add(
            responses_lib.GET,
            f"{MBTA_API_BASE}/stops",
            body=requests.exceptions.ConnectionError("timeout"),
        )
    with patch("time.sleep"):
        result = mbta_get("/stops", {})
    assert result == {"data": [], "included": []}


@responses_lib.activate
def test_retries_once_then_succeeds():
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/stops",
        body=requests.exceptions.ConnectionError("first attempt fails"),
    )
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/stops",
        json={"data": [{"id": "ok"}]},
        status=200,
    )
    with patch("time.sleep"):
        result = mbta_get("/stops", {})
    assert result == {"data": [{"id": "ok"}]}


@responses_lib.activate
def test_429_respects_retry_after_header():
    slept = []
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/stops",
        status=429,
        headers={"Retry-After": "10"},
    )
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/stops",
        json={"data": []},
        status=200,
    )
    with patch("time.sleep", side_effect=lambda x: slept.append(x)):
        result = mbta_get("/stops", {})
    assert result == {"data": []}
    assert 10 in slept


@responses_lib.activate
def test_429_without_retry_after_sleeps_1s():
    slept = []
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/stops",
        status=429,
    )
    responses_lib.add(
        responses_lib.GET,
        f"{MBTA_API_BASE}/stops",
        json={"data": []},
        status=200,
    )
    with patch("time.sleep", side_effect=lambda x: slept.append(x)):
        mbta_get("/stops", {})
    assert 1 in slept


@responses_lib.activate
def test_http_500_exhausts_retries_returns_empty():
    for _ in range(3):
        responses_lib.add(
            responses_lib.GET,
            f"{MBTA_API_BASE}/stops",
            status=500,
        )
    with patch("time.sleep"):
        result = mbta_get("/stops", {})
    assert result == {"data": [], "included": []}
