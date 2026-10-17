"""Sensors: receiver count per room / direct call, room count per intercom."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_CALLER_ID, ATTR_ROOM_ID
from .coordinator import AccessConfigEntry, AccessCoordinator
from .entity import TargetEntity, caller_device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AccessConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors and add new ones for new rooms."""
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        entities: list[SensorEntity] = []
        for caller in coordinator.data.callers.values():
            if (caller.id, "") not in known:
                known.add((caller.id, ""))
                entities.append(RoomCountSensor(coordinator, caller.id))
            for target in (caller.direct, *caller.rooms.values()):
                if (caller.id, target.id) not in known:
                    known.add((caller.id, target.id))
                    entities.append(ReceiverCountSensor(coordinator, caller.id, target.id))
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class ReceiverCountSensor(TargetEntity, SensorEntity):
    """Number of receivers of a room or of the direct call."""

    _attr_translation_key = "receivers"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AccessCoordinator, caller_id: str, target_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, caller_id, target_id)
        self._attr_unique_id = f"{caller_id}_{target_id}_receivers"

    @property
    def native_value(self) -> int | None:
        """Return the number of receivers."""
        target = self.target
        return len(target.receivers) if target else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the attached receivers and the room settings."""
        target = self.target
        if target is None:
            return {}
        attrs: dict[str, Any] = {
            ATTR_CALLER_ID: self.caller_id,
            ATTR_ROOM_ID: target.id,
            "receivers": [
                {
                    "type": r.type,
                    "id": r.id,
                    "name": self.coordinator.data.receiver_name(r.type, r.id) or r.name,
                    "schedule": r.schedule_name or "always",
                }
                for r in target.receivers
            ],
        }
        if not target.is_direct:
            attrs["room_number"] = target.number
            attrs["number_check"] = target.number_check
        return attrs


class RoomCountSensor(CoordinatorEntity[AccessCoordinator], SensorEntity):
    """Number of directory rooms of an intercom."""

    _attr_has_entity_name = True
    _attr_translation_key = "rooms"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AccessCoordinator, caller_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.caller_id = caller_id
        self._attr_unique_id = f"{caller_id}_rooms"
        self._attr_device_info = caller_device_info(coordinator.data.callers[caller_id])

    @property
    def available(self) -> bool:
        """Unavailable once the intercom is gone."""
        return super().available and self.caller_id in self.coordinator.data.callers

    @property
    def native_value(self) -> int | None:
        """Return the number of rooms."""
        caller = self.coordinator.data.callers.get(self.caller_id)
        return len(caller.rooms) if caller else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the rooms with their ids (handy for the services)."""
        caller = self.coordinator.data.callers.get(self.caller_id)
        if caller is None:
            return {}
        return {
            ATTR_CALLER_ID: caller.id,
            "directory_enabled": not caller.disable_directory,
            "rooms": [
                {"room_id": room.id, "name": room.name, "number": room.number}
                for room in caller.rooms.values()
            ],
        }
