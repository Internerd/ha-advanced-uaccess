"""Fixtures: a fake Access console kept in memory."""

from __future__ import annotations

from collections.abc import Generator
import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.advanced_uaccess.api import (
    AccessApiError,
    Receiver,
    Target,
    parse_topology,
)
from custom_components.advanced_uaccess.const import CONF_VERIFY_SSL, DOMAIN

CALLER_ID = "28704e31e640"
ROOM_A = "566126a3-2809-4993-9625-b1bfb6ebd6e0"
ROOM_B = "ec4877a7-10d7-4c38-8831-d823a829b870"
VIEWER = "0cea1417bf7d"
CHIME_WIFI = "E43883358449"
CHIME_POE = "E438830ED667"
USER_BOB = "cd4991c9-c4ad-42ab-827a-8fb9f4fd96f2"
USER_ALICE = "919a7a7a-497d-4451-8549-583944c9b988"
PHONE = "4b872767-c06d-424d-abf3-876f04119f8e"
SCHEDULE_ID = "296f707e-f338-4cfb-9eb8-2c35763a7865"

TOPOLOGY = json.loads((Path(__file__).parent / "fixtures" / "topology.json").read_text())

ENTRY_DATA = {
    "host": "192.168.55.10",
    "username": "ha-local",
    "password": "secret",
    CONF_VERIFY_SSL: False,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable custom integrations in all tests."""


class FakeClient:
    """Mimics AccessClient against an in-memory directory."""

    def __init__(self) -> None:
        rooms, direct, _ = parse_topology(CALLER_ID, TOPOLOGY)
        self.rooms: dict[str, Target] = rooms
        self.direct: Target = direct
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_delete = False
        self.schedules: dict[str, dict[str, Any]] = {
            SCHEDULE_ID: {
                "user_timezone": {
                    "week_schedule": {"1": [{"start_time": "09:00:00", "end_time": "17:00:59"}]}
                },
                "holiday_timezone": {"day_schedule": []},
            }
        }
        self._room_counter = 0

    # helpers
    def _target(self, target_id: str) -> Target:
        return self.direct if target_id == "direct" else self.rooms[target_id]

    def topology(self) -> dict[str, Any]:
        vertices = [
            {
                "node_id": "caller",
                "type": "caller",
                "extra": {"device_id": CALLER_ID, "disable_directory": False},
            }
        ]
        edges = []

        def add_receivers(parent: str, target: Target) -> None:
            for index, r in enumerate(target.receivers):
                node = f"{parent}-{index}"
                extra: dict[str, Any] = {
                    "schedule_id": r.schedule_id,
                    "schedule_is_private": r.schedule_is_private,
                    "schedule_name": r.schedule_name,
                }
                extra["id" if r.type == "admin" else "device_id"] = r.id
                vertices.append({"node_id": node, "type": r.type, "extra": extra})
                edges.append({"uplink_id": parent, "downlink_id": node})

        add_receivers("caller", self.direct)
        for room in self.rooms.values():
            node = f"room-{room.id}"
            vertices.append(
                {
                    "node_id": node,
                    "type": "directory",
                    "extra": {
                        "room_id": room.id,
                        "name": room.name,
                        "room": room.number,
                        "number_check": room.number_check,
                    },
                }
            )
            edges.append({"uplink_id": "caller", "downlink_id": node})
            add_receivers(node, room)
        return {"vertices": vertices, "edges": edges}

    # API surface
    async def login(self) -> None:
        self.calls.append(("login", ()))

    async def get_callers(self) -> list[dict[str, Any]]:
        return [
            {
                "unique_id": CALLER_ID,
                "name": "UA Intercom e640",
                "alias": "Main Gate",
                "device_type": "UA-Intercom",
                "firmware": "v1.11.23.0",
                "is_online": True,
            }
        ]

    async def get_topology(self, caller_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.topology())

    async def get_viewers(self) -> dict[str, str]:
        return {VIEWER: "Front door"}

    async def get_chimes(self) -> dict[str, str]:
        return {CHIME_WIFI: "WiFi Chime", CHIME_POE: "PoE Chime"}

    async def get_users(self) -> dict[str, str]:
        return {USER_BOB: "Bob Example", USER_ALICE: "Alice Example"}

    async def get_phone_numbers(self) -> dict[str, str]:
        return {PHONE: "+49 176 xxx973"}

    async def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        return self.schedules[schedule_id]

    async def set_room_receivers(self, caller_id, room, receivers) -> None:
        receivers = list(receivers)
        self.calls.append(("set_room_receivers", (room.id, [r.key for r in receivers])))
        self._replace(self.rooms[room.id], receivers)

    async def set_direct_receivers(self, caller_id, receivers) -> None:
        receivers = list(receivers)
        self.calls.append(("set_direct_receivers", ([r.key for r in receivers],)))
        self._replace(self.direct, receivers)

    def _replace(self, target: Target, receivers: list[Receiver]) -> None:
        old = {r.key: r for r in target.receivers}
        # Access keeps schedules of receivers that stay, new ones are "always".
        target.receivers = [old.get(r.key, Receiver(r.type, r.id)) for r in receivers]

    async def remove_receiver(self, caller_id, target_id, receiver_type, receiver_id) -> None:
        self.calls.append(("remove_receiver", (target_id, receiver_type, receiver_id)))
        if self.fail_delete:
            raise AccessApiError(0, "CODE_FAIL", "nope")
        target = self._target(target_id)
        target.receivers = [r for r in target.receivers if r.key != (receiver_type, receiver_id)]

    async def set_schedule(
        self,
        caller_id,
        target_id,
        receiver_type,
        receiver_id,
        week,
        holiday=None,
        name=None,
        stamp=0,
    ) -> None:
        self.calls.append(("set_schedule", (target_id, receiver_type, receiver_id, week)))
        receiver = self._target(target_id).find(receiver_type, receiver_id)
        receiver.schedule_id = "new-schedule"
        receiver.schedule_is_private = True
        receiver.schedule_name = "Custom"

    async def clear_schedule(self, caller_id, target_id, receiver_type, receiver_id) -> None:
        self.calls.append(("clear_schedule", (target_id, receiver_type, receiver_id)))
        receiver = self._target(target_id).find(receiver_type, receiver_id)
        receiver.schedule_id = ""
        receiver.schedule_is_private = False
        receiver.schedule_name = ""

    async def create_room(self, caller_id, name, number, number_check, receivers) -> dict[str, Any]:
        self._room_counter += 1
        room_id = f"new-room-{self._room_counter}"
        self.calls.append(("create_room", (name, number, [r.key for r in receivers])))
        self.rooms[room_id] = Target(room_id, name, number, number_check, list(receivers))
        return {"id": room_id, "name": name, "room": number}

    async def delete_room(self, caller_id, room_id) -> None:
        self.calls.append(("delete_room", (room_id,)))
        del self.rooms[room_id]


@pytest.fixture
def fake_client() -> Generator[FakeClient]:
    """Patch the client factory with the fake console."""
    client = FakeClient()
    with (
        patch("custom_components.advanced_uaccess.create_client", return_value=client),
        patch(
            "custom_components.advanced_uaccess.config_flow.create_client",
            return_value=client,
        ),
    ):
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the fake console."""
    return MockConfigEntry(
        domain=DOMAIN, data=dict(ENTRY_DATA), unique_id=ENTRY_DATA["host"], title="Console"
    )
