"""Call events per room and for the doorbell direct call."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .calls import CALL_EVENT_TYPES, Call
from .coordinator import AccessConfigEntry, AccessCoordinator
from .entity import TargetEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AccessConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one call event per room and per direct call."""
    coordinator = entry.runtime_data
    if coordinator.calls is None:
        return
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        entities = []
        for caller in coordinator.data.callers.values():
            for target in (caller.direct, *caller.rooms.values()):
                if (caller.id, target.id) not in known:
                    known.add((caller.id, target.id))
                    entities.append(CallEvent(coordinator, caller.id, target.id))
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class CallEvent(TargetEntity, EventEntity):
    """Fires when this room (or the doorbell call) is rung, answered, missed …"""

    _attr_translation_key = "call"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = CALL_EVENT_TYPES

    def __init__(self, coordinator: AccessCoordinator, caller_id: str, target_id: str) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, caller_id, target_id)
        self._attr_unique_id = f"{caller_id}_{target_id}_call"

    async def async_added_to_hass(self) -> None:
        """Subscribe to call events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.coordinator.call_signal, self._on_call)
        )

    @callback
    def _on_call(self, kind: str, call: Call, data: dict[str, Any]) -> None:
        if call.caller_id != self.caller_id or call.target_id != self.target_id:
            return
        self._trigger_event(kind, data)
        self.async_write_ha_state()
