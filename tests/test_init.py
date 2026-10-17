"""Integration tests against the fake console."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.advanced_uaccess.const import CONF_USER_RECEIVERS, DOMAIN

from .conftest import (
    CALLER_ID,
    CHIME_POE,
    CHIME_WIFI,
    PHONE,
    ROOM_A,
    ROOM_B,
    USER_ALICE,
    USER_BOB,
    VIEWER,
    FakeClient,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _switch(hass: HomeAssistant, target: str, rtype: str, rid: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{CALLER_ID}_{target}_{rtype}_{rid}"
    )
    assert entity_id is not None
    return entity_id


async def test_setup_creates_entities(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Switches reflect the topology; devices exist per room and direct call."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    assert hass.states.get(_switch(hass, ROOM_A, "viewer", VIEWER)).state == "on"
    assert hass.states.get(_switch(hass, ROOM_A, "chime", CHIME_WIFI)).state == "on"
    assert hass.states.get(_switch(hass, ROOM_A, "chime", CHIME_POE)).state == "off"
    assert hass.states.get(_switch(hass, ROOM_B, "admin", USER_BOB)).state == "on"
    assert hass.states.get(_switch(hass, ROOM_A, "phone_number", PHONE)).state == "off"
    direct = hass.states.get(_switch(hass, "direct", "viewer", VIEWER))
    assert direct.state == "on"
    assert direct.attributes["schedule"] == "Custom"
    assert direct.attributes["receiver_type"] == "viewer"

    # Users only appear when they already receive calls (default option).
    assert (
        er.async_get(hass).async_get_entity_id(
            "switch", DOMAIN, f"{CALLER_ID}_{ROOM_A}_admin_{USER_ALICE}"
        )
        is None
    )

    devices = dr.async_get(hass)
    assert devices.async_get_device({(DOMAIN, CALLER_ID)}).name == "Main Gate"
    assert devices.async_get_device({(DOMAIN, f"{CALLER_ID}_{ROOM_A}")}).name == "1 Alice"
    assert devices.async_get_device({(DOMAIN, f"{CALLER_ID}_direct")}) is not None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_switch_on_off_room(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Turning on replaces the list, turning off uses the granular delete."""
    await _setup(hass, config_entry)
    entity_id = _switch(hass, ROOM_A, "chime", CHIME_POE)

    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    assert hass.states.get(entity_id).state == "on"
    assert (
        "set_room_receivers",
        (ROOM_A, [("chime", CHIME_WIFI), ("viewer", VIEWER), ("chime", CHIME_POE)]),
    ) in fake_client.calls

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    assert hass.states.get(entity_id).state == "off"
    assert ("remove_receiver", (ROOM_A, "chime", CHIME_POE)) in fake_client.calls


async def test_remove_falls_back_to_list(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """If the granular DELETE is rejected, the list replace is used."""
    await _setup(hass, config_entry)
    fake_client.fail_delete = True
    entity_id = _switch(hass, ROOM_A, "viewer", VIEWER)
    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    assert hass.states.get(entity_id).state == "off"
    assert ("set_room_receivers", (ROOM_A, [("chime", CHIME_WIFI)])) in fake_client.calls


async def test_direct_call_schedule_restore(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """The private schedule is saved on removal and restored when re-added."""
    await _setup(hass, config_entry)
    entity_id = _switch(hass, "direct", "viewer", VIEWER)

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    assert hass.states.get(entity_id).state == "off"

    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    assert ("set_direct_receivers", ([("viewer", VIEWER)],)) in fake_client.calls
    restore = [c for c in fake_client.calls if c[0] == "set_schedule"]
    assert restore == [
        (
            "set_schedule",
            (
                "direct",
                "viewer",
                VIEWER,
                {
                    "0": [],
                    "1": [{"start_time": "09:00:00", "end_time": "17:00:59"}],
                    "2": [],
                    "3": [],
                    "4": [],
                    "5": [],
                    "6": [],
                },
            ),
        )
    ]
    assert hass.states.get(entity_id).attributes["schedule"] == "Custom"


async def test_services(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Room and schedule services."""
    await _setup(hass, config_entry)

    result = await hass.services.async_call(
        DOMAIN,
        "create_room",
        {"name": "3 Guest", "number": 999, "chimes": ["e4:38:83:35:84:49"]},
        blocking=True,
        return_response=True,
    )
    room_id = result["room_id"]
    await hass.async_block_till_done()
    assert hass.states.get(_switch(hass, room_id, "chime", CHIME_WIFI)).state == "on"

    await hass.services.async_call(
        DOMAIN,
        "add_receiver",
        {"room_id": room_id, "receiver_type": "phone_number", "receiver_id": PHONE},
        blocking=True,
    )
    assert hass.states.get(_switch(hass, room_id, "phone_number", PHONE)).state == "on"

    entity_id = _switch(hass, room_id, "chime", CHIME_WIFI)
    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "entity_id": entity_id,
            "schedule": {"monday": ["09:00-17:00"], "friday": [{"start": "8:30", "end": "12:00"}]},
        },
        blocking=True,
    )
    week = [c for c in fake_client.calls if c[0] == "set_schedule"][-1][1][3]
    assert week["1"] == [{"start_time": "09:00:00", "end_time": "17:00:59"}]
    assert week["5"] == [{"start_time": "08:30:00", "end_time": "12:00:59"}]

    await hass.services.async_call(
        DOMAIN, "clear_schedule", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).attributes["schedule"] == "always"

    await hass.services.async_call(
        DOMAIN, "remove_receiver", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == "off"

    await hass.services.async_call(DOMAIN, "delete_room", {"room_id": room_id}, blocking=True)
    await hass.async_block_till_done()
    assert dr.async_get(hass).async_get_device({(DOMAIN, f"{CALLER_ID}_{room_id}")}) is None

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "delete_room", {"room_id": "nope"}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {
                "entity_id": _switch(hass, ROOM_A, "viewer", VIEWER),
                "schedule": {"monday": ["25:00-26:00"]},
            },
            blocking=True,
        )


async def test_user_receivers_option(hass: HomeAssistant, fake_client: FakeClient) -> None:
    """Selected users get switches in every room."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "h", "username": "u", "password": "p", "verify_ssl": False},
        options={CONF_USER_RECEIVERS: [USER_ALICE]},
    )
    await _setup(hass, entry)
    assert hass.states.get(_switch(hass, ROOM_A, "admin", USER_ALICE)).state == "off"
    # Bob still has a switch where he is attached.
    assert hass.states.get(_switch(hass, ROOM_B, "admin", USER_BOB)).state == "on"
