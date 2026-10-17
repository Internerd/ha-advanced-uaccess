"""Call tracking from the ring history."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.advanced_uaccess.calls import EVENT_CALL, classify, parse_ring_event
from custom_components.advanced_uaccess.const import CONF_TRACK_CALLS, DOMAIN

from .conftest import CALLER_ID, ROOM_B, FakeClient, ring_event


def _entity(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


def test_classify() -> None:
    """Recorded and UI-source log keys map to call event types."""
    assert classify("access.visitor.made.doorbell.to.viewer", "access.remotecall.request") == (
        "incoming"
    )
    assert classify("access.doorbell.visitor.rang.remotecall", "access.remotecall.request") == (
        "incoming"
    )
    assert classify("access.user.accept.doorbell.door_to_viewer", "access.remotecall.accepted") == (
        "accepted"
    )
    assert classify("access.doorbell.phone.accept", "") == "accepted"
    assert classify("access.user.reject.doorbell.from_door_to_viewer", "access.door.unlock") == (
        "rejected"
    )
    assert classify("access.doorbell.reject.use.viewer", "access.door.unlock") == "rejected"
    assert classify("access.doorbell.missed.from_door_to_viewer", "") == "missed"
    assert classify("access.doorbell.no_receivers.missed", "") == "missed"
    assert classify("access.doorbell.canceled.from_door_to_viewer", "") == "canceled"
    assert classify("access.something.else", "") is None


def test_parse_ring_event() -> None:
    """Room, actor and capture are taken from the metadata."""
    entry = parse_ring_event(
        ring_event(
            1,
            "access.visitor.made.doorbell.to.viewer",
            "access.remotecall.request",
            1_700_000_000_000,
            room=(ROOM_B, "2 Bob"),
            capture="protect_x",
        )
    )
    assert entry.caller_id == CALLER_ID
    assert entry.target_id == ROOM_B
    assert entry.target_name == "2 Bob"
    assert entry.call_id == "protect_x"
    assert entry.kind == "incoming"
    direct = parse_ring_event(
        ring_event(2, "access.doorbell.visitor.rang.remotecall", "", 1, viewer=True)
    )
    assert direct.target_id == "direct"
    assert parse_ring_event({"id": "3"}) is None


async def test_call_flow(hass: HomeAssistant, fake_client: FakeClient) -> None:
    """Replays the recorded sequence 5645-5651 (accept + hang up, then a missed doorbell)."""
    now_ms = int(dt_util.utcnow().timestamp() * 1000)
    # An old call before setup is known but not replayed as event.
    fake_client.ring_events = [
        ring_event(
            5600,
            "access.visitor.made.doorbell.to.viewer",
            "access.remotecall.request",
            now_ms - 3_600_000,
            room=(ROOM_B, "2 Bob"),
            capture="old",
        )
    ]
    entry = MockConfigEntry(
        domain=DOMAIN, data={"host": "h", "username": "u", "password": "p", "verify_ssl": False}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    bus: list[Any] = async_capture_events(hass, EVENT_CALL)

    last_call = _entity(hass, "sensor", f"{CALLER_ID}_last_call")
    assert hass.states.get(last_call).state == "2 Bob"
    room_event = _entity(hass, "event", f"{CALLER_ID}_{ROOM_B}_call")
    direct_event = _entity(hass, "event", f"{CALLER_ID}_direct_call")
    assert hass.states.get(room_event).state == "unknown"

    fake_client.ring_events += [
        ring_event(
            5645,
            "access.visitor.made.doorbell.to.viewer",
            "access.remotecall.request",
            now_ms - 30_000,
            room=(ROOM_B, "2 Bob"),
            capture="call-1",
        ),
        ring_event(
            5646,
            "access.user.accept.doorbell.door_to_viewer",
            "access.remotecall.accepted",
            now_ms - 25_000,
            room=(ROOM_B, "2 Bob"),
            actor="Bob Example",
        ),
        ring_event(
            5647,
            "access.user.reject.doorbell.from_door_to_viewer",
            "access.door.unlock",
            now_ms - 10_000,
            room=(ROOM_B, "2 Bob"),
            actor="Bob Example",
            capture="call-1",
        ),
    ]
    await entry.runtime_data.calls.async_refresh()
    await hass.async_block_till_done()

    assert [e.data["type"] for e in bus] == ["incoming", "accepted", "ended"]
    assert bus[1].data["answered_by"] == "Bob Example"
    state = hass.states.get(room_event)
    assert state.attributes["event_type"] == "ended"
    assert state.attributes["status"] == "accepted"
    assert state.attributes["answered_by"] == "Bob Example"
    sensor = hass.states.get(last_call)
    assert sensor.state == "2 Bob"
    assert sensor.attributes["status"] == "accepted"
    assert sensor.attributes["call_id"] == "call-1"

    fake_client.ring_events += [
        ring_event(
            5650,
            "access.doorbell.visitor.rang.remotecall",
            "access.remotecall.request",
            now_ms - 5_000,
            capture="call-2",
            viewer=True,
        ),
        ring_event(
            5651,
            "access.doorbell.no_receivers.missed",
            "access.door.unlock",
            now_ms - 1_000,
            capture="call-2",
        ),
    ]
    await entry.runtime_data.calls.async_refresh()
    await hass.async_block_till_done()
    assert [e.data["type"] for e in bus][3:] == ["incoming", "missed"]
    assert hass.states.get(direct_event).attributes["event_type"] == "missed"
    sensor = hass.states.get(last_call)
    assert sensor.state == "Main Gate doorbell call"
    assert sensor.attributes["room_id"] == "direct"
    assert sensor.attributes["status"] == "missed"

    # Nothing is emitted twice.
    await entry.runtime_data.calls.async_refresh()
    assert len(bus) == 5
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_tracking_disabled(hass: HomeAssistant, fake_client: FakeClient) -> None:
    """Without call tracking no event entities or history requests exist."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "h", "username": "u", "password": "p", "verify_ssl": False},
        options={CONF_TRACK_CALLS: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.states.async_entity_ids("event")
    assert ("search_system_log", ("rings",)) not in fake_client.calls
    assert await hass.config_entries.async_unload(entry.entry_id)
