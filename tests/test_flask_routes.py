import pytest
from mbta_tracker import app, _display_data, _display_lock


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200


def test_data_endpoint_returns_json(client):
    with _display_lock:
        _display_data["stations"] = [{"name": "Test Station"}]
        _display_data["last_updated"] = "January 1, 2026"
        _display_data["error"] = None

    response = client.get("/data")
    assert response.status_code == 200
    assert response.content_type == "application/json"
    body = response.get_json()
    assert body["stations"] == [{"name": "Test Station"}]
    assert body["last_updated"] == "January 1, 2026"
    assert body["error"] is None


def test_data_endpoint_reflects_error_state(client):
    with _display_lock:
        _display_data["error"] = "something went wrong"

    response = client.get("/data")
    assert response.get_json()["error"] == "something went wrong"
