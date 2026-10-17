"""Services of the Advanced UniFi Access Directory integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .api import Receiver
from .const import (
    ATTR_ADMINS,
    ATTR_CALLER_ID,
    ATTR_CHIMES,
    ATTR_NAME,
    ATTR_NUMBER,
    ATTR_NUMBER_CHECK,
    ATTR_PHONE_NUMBERS,
    ATTR_RECEIVER_ID,
    ATTR_RECEIVER_TYPE,
    ATTR_ROOM_ID,
    ATTR_SCHEDULE,
    ATTR_VIEWERS,
    DOMAIN,
    RECEIVER_TYPES,
    SERVICE_ADD_RECEIVER,
    SERVICE_CLEAR_SCHEDULE,
    SERVICE_CREATE_ROOM,
    SERVICE_DELETE_ROOM,
    SERVICE_REMOVE_RECEIVER,
    SERVICE_SET_SCHEDULE,
    TYPE_ADMIN,
    TYPE_CHIME,
    TYPE_PHONE,
    TYPE_VIEWER,
    WEEKDAYS,
)
from .coordinator import AccessCoordinator

_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")

RECEIVER_FIELDS = {
    vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
    vol.Optional(ATTR_CALLER_ID): cv.string,
    vol.Optional(ATTR_ROOM_ID): cv.string,
    vol.Optional(ATTR_RECEIVER_TYPE): vol.In(RECEIVER_TYPES),
    vol.Optional(ATTR_RECEIVER_ID): cv.string,
}

ID_LIST = vol.All(cv.ensure_list, [cv.string])

SCHEDULE_SCHEMA = vol.Schema(
    {vol.Optional(day): vol.All(cv.ensure_list, [vol.Any(cv.string, dict)]) for day in WEEKDAYS}
)


def _coordinators(hass: HomeAssistant) -> list[AccessCoordinator]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _coordinator_for(hass: HomeAssistant, caller_id: str | None) -> AccessCoordinator:
    coordinators = _coordinators(hass)
    if caller_id is None:
        callers = [
            (c, caller_id) for c in coordinators for caller_id in (c.data.callers if c.data else {})
        ]
        if len(callers) == 1:
            return callers[0][0]
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="caller_required")
    for coordinator in coordinators:
        if coordinator.data and caller_id in coordinator.data.callers:
            return coordinator
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_caller",
        translation_placeholders={"caller_id": caller_id},
    )


def _resolve_receiver(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[AccessCoordinator, str, str, str, str]:
    """Return coordinator, caller, target, type and id from entity or fields."""
    values = dict(data)
    if entity_id := values.get(ATTR_ENTITY_ID):
        state = hass.states.get(entity_id)
        if state is None or ATTR_RECEIVER_ID not in state.attributes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_entity",
                translation_placeholders={"entity_id": entity_id},
            )
        for key in (ATTR_CALLER_ID, ATTR_ROOM_ID, ATTR_RECEIVER_TYPE, ATTR_RECEIVER_ID):
            values[key] = state.attributes[key]
    missing = [
        key for key in (ATTR_ROOM_ID, ATTR_RECEIVER_TYPE, ATTR_RECEIVER_ID) if not values.get(key)
    ]
    if missing:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="missing_fields",
            translation_placeholders={"fields": ", ".join(missing)},
        )
    coordinator = _coordinator_for(hass, values.get(ATTR_CALLER_ID))
    caller = coordinator.caller(values.get(ATTR_CALLER_ID))
    if caller.target(values[ATTR_ROOM_ID]) is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_room",
            translation_placeholders={"room_id": values[ATTR_ROOM_ID]},
        )
    receiver_type = values[ATTR_RECEIVER_TYPE]
    receiver_id = str(values[ATTR_RECEIVER_ID])
    if receiver_type == TYPE_CHIME:
        receiver_id = normalize_mac(receiver_id)
    return coordinator, caller.id, values[ATTR_ROOM_ID], receiver_type, receiver_id


def normalize_mac(value: str) -> str:
    """Chimes are listed by MAC without separators, upper case."""
    return value.replace(":", "").replace("-", "").upper()


def _normalize_time(value: str, end: bool) -> str:
    match = _TIME.match(value.strip())
    if not match:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_time",
            translation_placeholders={"value": value},
        )
    hour, minute, second = int(match[1]), int(match[2]), match[3]
    if hour > 23 or minute > 59:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_time",
            translation_placeholders={"value": value},
        )
    # The Access UI ends a slot at hh:mm:59.
    sec = int(second) if second is not None else (59 if end else 0)
    return f"{hour:02d}:{minute:02d}:{sec:02d}"


def parse_week_schedule(schedule: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Convert {monday: ["09:00-17:00", {start, end}]} to the API format."""
    week: dict[str, list[dict[str, str]]] = {str(i): [] for i in range(7)}
    for index, day in enumerate(WEEKDAYS):
        for slot in schedule.get(day) or []:
            if isinstance(slot, str):
                start, sep, end = slot.partition("-")
                if not sep:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="invalid_time",
                        translation_placeholders={"value": slot},
                    )
            else:
                start, end = str(slot.get("start", "")), str(slot.get("end", ""))
            week[str(index)].append(
                {
                    "start_time": _normalize_time(start, end=False),
                    "end_time": _normalize_time(end, end=True),
                }
            )
    return week


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register all services."""

    async def add_receiver(call: ServiceCall) -> None:
        coordinator, *ids = _resolve_receiver(hass, call.data)
        await coordinator.async_add_receiver(*ids)

    async def remove_receiver(call: ServiceCall) -> None:
        coordinator, *ids = _resolve_receiver(hass, call.data)
        await coordinator.async_remove_receiver(*ids)

    async def set_schedule(call: ServiceCall) -> None:
        coordinator, *ids = _resolve_receiver(hass, call.data)
        week = parse_week_schedule(call.data[ATTR_SCHEDULE])
        await coordinator.async_set_schedule(*ids, week)

    async def clear_schedule(call: ServiceCall) -> None:
        coordinator, *ids = _resolve_receiver(hass, call.data)
        await coordinator.async_clear_schedule(*ids)

    async def create_room(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator_for(hass, call.data.get(ATTR_CALLER_ID))
        caller = coordinator.caller(call.data.get(ATTR_CALLER_ID))
        receivers = [
            Receiver(
                receiver_type,
                normalize_mac(receiver_id) if receiver_type == TYPE_CHIME else receiver_id,
            )
            for field, receiver_type in (
                (ATTR_VIEWERS, TYPE_VIEWER),
                (ATTR_ADMINS, TYPE_ADMIN),
                (ATTR_CHIMES, TYPE_CHIME),
                (ATTR_PHONE_NUMBERS, TYPE_PHONE),
            )
            for receiver_id in call.data.get(field, [])
        ]
        room = await coordinator.async_create_room(
            caller.id,
            call.data[ATTR_NAME],
            str(call.data[ATTR_NUMBER]),
            call.data[ATTR_NUMBER_CHECK],
            receivers,
        )
        return {"room_id": room.get("id"), "caller_id": caller.id}

    async def delete_room(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data.get(ATTR_CALLER_ID))
        caller = coordinator.caller(call.data.get(ATTR_CALLER_ID))
        room_id = call.data[ATTR_ROOM_ID]
        if room_id not in caller.rooms:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_room",
                translation_placeholders={"room_id": room_id},
            )
        await coordinator.async_delete_room(caller.id, room_id)

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_RECEIVER, add_receiver, vol.Schema(RECEIVER_FIELDS)
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_RECEIVER, remove_receiver, vol.Schema(RECEIVER_FIELDS)
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        set_schedule,
        vol.Schema({**RECEIVER_FIELDS, vol.Required(ATTR_SCHEDULE): SCHEDULE_SCHEMA}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SCHEDULE, clear_schedule, vol.Schema(RECEIVER_FIELDS)
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_ROOM,
        create_room,
        vol.Schema(
            {
                vol.Optional(ATTR_CALLER_ID): cv.string,
                vol.Required(ATTR_NAME): cv.string,
                vol.Required(ATTR_NUMBER): vol.All(vol.Coerce(str), vol.Match(r"^\d+$")),
                vol.Optional(ATTR_NUMBER_CHECK, default=True): cv.boolean,
                vol.Optional(ATTR_VIEWERS, default=[]): ID_LIST,
                vol.Optional(ATTR_ADMINS, default=[]): ID_LIST,
                vol.Optional(ATTR_CHIMES, default=[]): ID_LIST,
                vol.Optional(ATTR_PHONE_NUMBERS, default=[]): ID_LIST,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_ROOM,
        delete_room,
        vol.Schema(
            {
                vol.Optional(ATTR_CALLER_ID): cv.string,
                vol.Required(ATTR_ROOM_ID): cv.string,
            }
        ),
    )
