"""Tests for the API helpers (no Home Assistant needed)."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.advanced_uaccess.api import (
    Receiver,
    _find_viewers,
    extract_week_schedule,
    format_phone,
    parse_topology,
    receiver_groups,
)
from custom_components.advanced_uaccess.const import DIRECT_CALL, api_receiver_type

TOPOLOGY = json.loads((Path(__file__).parent / "fixtures" / "topology.json").read_text())
ROOM_A = "566126a3-2809-4993-9625-b1bfb6ebd6e0"
ROOM_B = "ec4877a7-10d7-4c38-8831-d823a829b870"


def test_parse_topology() -> None:
    """Rooms and direct-call receivers are separated by their parent node."""
    rooms, direct, extra = parse_topology("28704e31e640", TOPOLOGY)
    assert extra["device_id"] == "28704e31e640"
    assert set(rooms) == {ROOM_A, ROOM_B}
    assert rooms[ROOM_A].number == "255"
    assert [r.key for r in rooms[ROOM_A].receivers] == [
        ("chime", "E43883358449"),
        ("viewer", "0cea1417bf7d"),
    ]
    assert [r.key for r in rooms[ROOM_B].receivers] == [
        ("admin", "cd4991c9-c4ad-42ab-827a-8fb9f4fd96f2"),
        ("chime", "E438830ED667"),
    ]
    assert direct.id == DIRECT_CALL
    (viewer,) = direct.receivers
    assert viewer.key == ("viewer", "0cea1417bf7d")
    assert viewer.schedule_is_private
    assert viewer.schedule_id == "296f707e-f338-4cfb-9eb8-2c35763a7865"


def test_parse_empty_topology() -> None:
    """A topology without caller vertex yields nothing."""
    rooms, direct, extra = parse_topology("x", {})
    assert rooms == {} and direct.receivers == [] and extra == {}


def test_receiver_groups_payload() -> None:
    """The payload contains every list key, ids grouped by type."""
    payload = receiver_groups(
        [
            Receiver("viewer", "0cea1417bf7d"),
            Receiver("chime", "E43883358449"),
            Receiver("chime", "E43883358449"),
            Receiver("phone_number", "4b87"),
        ]
    )
    assert payload == [
        {
            "viewers": ["0cea1417bf7d"],
            "admins": [],
            "chimes": ["E43883358449"],
            "phone_numbers": ["4b87"],
            "third_party_sips": [],
            "third_party_viewers": [],
        }
    ]


def test_api_receiver_type() -> None:
    """viewer is sent as viewer_device, everything else unchanged."""
    assert api_receiver_type("viewer") == "viewer_device"
    assert api_receiver_type("chime") == "chime"
    assert api_receiver_type("admin") == "admin"


def test_extract_week_schedule() -> None:
    """week_schedule and holiday slots are found anywhere in the response."""
    week, holiday = extract_week_schedule(
        {
            "id": "x",
            "user_timezone": {
                "week_schedule": {
                    "1": [{"start_time": "09:00:00", "end_time": "17:00:59", "id": 3}]
                }
            },
            "holiday_timezone": {
                "day_schedule": [{"start_time": "10:00:00", "end_time": "11:00:59"}]
            },
        }
    )
    assert week["1"] == [{"start_time": "09:00:00", "end_time": "17:00:59"}]
    assert week["0"] == [] and len(week) == 7
    assert holiday == [{"start_time": "10:00:00", "end_time": "11:00:59"}]
    assert extract_week_schedule({"foo": "bar"}) is None


def test_find_viewers() -> None:
    """Viewers are collected from nested device groups."""
    found: dict[str, str] = {}
    _find_viewers(
        [
            {
                "device_groups": [
                    [
                        {
                            "unique_id": "0cea1417bf7d",
                            "alias": "Front door",
                            "device_type": "UA-Int-Viewer",
                        },
                        {
                            "unique_id": "28704e31e640",
                            "alias": "Gate",
                            "device_type": "UA-Intercom",
                        },
                    ]
                ]
            }
        ],
        found,
    )
    assert found == {"0cea1417bf7d": "Front door"}


def test_format_phone() -> None:
    """The trunk 0 is dropped after an international prefix."""
    assert format_phone("+49", "0176 123") == "+49 176 123"
    assert format_phone("+49", "160 456") == "+49 160 456"
    assert format_phone("", "0176 123") == "0176 123"
    assert format_phone(None, None) == ""
