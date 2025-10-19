import responses
from mbta_tracker import get_route_direction_map, MBTA_API_BASE

@responses.activate
def test_direction_map_standard_inbound_outbound():
    rid = "Red"
    responses.add(
        responses.GET,
        f"{MBTA_API_BASE}/routes/{rid}",
        json={"data": {"attributes": {"direction_names": ["Outbound", "Inbound"]}}},
        status=200,
    )

    mapping = get_route_direction_map(rid)
    assert mapping["outbound"] == 0
    assert mapping["inbound"] == 1


@responses.activate
def test_direction_map_missing_fields_defaults():
    rid = "Blue"
    responses.add(
        responses.GET,
        f"{MBTA_API_BASE}/routes/{rid}",
        json={},  # broken response
        status=500,
    )

    mapping = get_route_direction_map(rid)
    # Should still return integers and not crash
    assert "inbound" in mapping
    assert "outbound" in mapping
    assert isinstance(mapping["inbound"], int)
