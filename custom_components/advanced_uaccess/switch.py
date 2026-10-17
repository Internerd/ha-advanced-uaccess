"""Switches: one per (room or direct call) x receiver."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ATTR_CALLER_ID,
    ATTR_RECEIVER_ID,
    ATTR_RECEIVER_TYPE,
    ATTR_ROOM_ID,
    CONF_USER_RECEIVERS,
    MANAGEABLE_TYPES,
    TYPE_ADMIN,
)
from .coordinator import AccessConfigEntry, AccessCoordinator, AccessData
from .entity import TargetEntity

PARALLEL_UPDATES = 1

type SwitchKey = tuple[str, str, str, str]  # caller, target, type, receiver id


def _wanted_keys(data: AccessData, user_receivers: list[str] | None) -> set[SwitchKey]:
    """Return every switch that should exist for the current data."""
    present: set[SwitchKey] = set()
    for caller in data.callers.values():
        for target in (caller.direct, *caller.rooms.values()):
            present.update((caller.id, target.id, r.type, r.id) for r in target.receivers)

    if user_receivers is None:
        # Default: users that already receive calls somewhere.
        users = {key[3] for key in present if key[2] == TYPE_ADMIN}
    else:
        users = set(user_receivers)

    wanted = set(present)
    for caller in data.callers.values():
        for target in (caller.direct, *caller.rooms.values()):
            for receiver_type in MANAGEABLE_TYPES:
                for receiver_id in data.catalog.get(receiver_type, {}):
                    if receiver_type == TYPE_ADMIN and receiver_id not in users:
                        continue
                    wanted.add((caller.id, target.id, receiver_type, receiver_id))
    return wanted


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AccessConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up receiver switches and add new ones as the directory changes."""
    coordinator = entry.runtime_data
    user_receivers = entry.options.get(CONF_USER_RECEIVERS)
    known: set[SwitchKey] = set()

    @callback
    def _add_new() -> None:
        new = _wanted_keys(coordinator.data, user_receivers) - known
        if not new:
            return
        known.update(new)
        async_add_entities(ReceiverSwitch(coordinator, *key) for key in sorted(new))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class ReceiverSwitch(TargetEntity, SwitchEntity):
    """On = the receiver rings for this room (or the doorbell direct call)."""

    def __init__(
        self,
        coordinator: AccessCoordinator,
        caller_id: str,
        target_id: str,
        receiver_type: str,
        receiver_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, caller_id, target_id)
        self.receiver_type = receiver_type
        self.receiver_id = receiver_id
        self._attr_translation_key = receiver_type
        self._attr_unique_id = f"{caller_id}_{target_id}_{receiver_type}_{receiver_id}"
        self._attr_name = self._receiver_name()

    def _receiver_name(self) -> str:
        name = self.coordinator.data.receiver_name(self.receiver_type, self.receiver_id)
        if not name and (target := self.target):
            receiver = target.find(self.receiver_type, self.receiver_id)
            name = receiver.name if receiver else ""
        return name or self.receiver_id

    @property
    def is_on(self) -> bool | None:
        """Return True if the receiver is attached."""
        target = self.target
        if target is None:
            return None
        return target.find(self.receiver_type, self.receiver_id) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the ids needed by the services, plus the schedule."""
        attrs: dict[str, Any] = {
            ATTR_CALLER_ID: self.caller_id,
            ATTR_ROOM_ID: self.target_id,
            ATTR_RECEIVER_TYPE: self.receiver_type,
            ATTR_RECEIVER_ID: self.receiver_id,
        }
        target = self.target
        receiver = target.find(self.receiver_type, self.receiver_id) if target else None
        if receiver is not None:
            attrs["schedule"] = receiver.schedule_name or "always"
            attrs["schedule_is_private"] = receiver.schedule_is_private
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Attach the receiver."""
        await self.coordinator.async_add_receiver(
            self.caller_id, self.target_id, self.receiver_type, self.receiver_id
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Detach the receiver."""
        await self.coordinator.async_remove_receiver(
            self.caller_id, self.target_id, self.receiver_type, self.receiver_id
        )
