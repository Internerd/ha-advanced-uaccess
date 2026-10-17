"""Base entities for the Advanced UniFi Access Directory integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Caller, Target
from .const import DIRECT_CALL, DOMAIN
from .coordinator import AccessCoordinator


def caller_device_info(caller: Caller) -> DeviceInfo:
    """Device for an intercom."""
    return DeviceInfo(
        identifiers={(DOMAIN, caller.id)},
        manufacturer="Ubiquiti",
        model=caller.model or None,
        name=caller.display_name,
        sw_version=caller.firmware or None,
    )


def target_device_info(caller: Caller, target: Target) -> DeviceInfo:
    """Device for a directory room or for the direct call of an intercom."""
    if target.id == DIRECT_CALL:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{caller.id}_{DIRECT_CALL}")},
            translation_key="direct_call",
            translation_placeholders={"intercom": caller.display_name},
            via_device=(DOMAIN, caller.id),
        )
    return DeviceInfo(
        identifiers={(DOMAIN, f"{caller.id}_{target.id}")},
        name=target.name,
        model=f"Room {target.number}" if target.number else None,
        via_device=(DOMAIN, caller.id),
    )


class TargetEntity(CoordinatorEntity[AccessCoordinator]):
    """Entity that belongs to a room or to the direct call."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AccessCoordinator, caller_id: str, target_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.caller_id = caller_id
        self.target_id = target_id
        caller = coordinator.data.callers[caller_id]
        target = caller.target(target_id)
        assert target is not None
        self._attr_device_info = target_device_info(caller, target)

    @property
    def caller(self) -> Caller | None:
        """Return the current caller."""
        return self.coordinator.data.callers.get(self.caller_id)

    @property
    def target(self) -> Target | None:
        """Return the current room or direct call."""
        caller = self.caller
        return caller.target(self.target_id) if caller else None

    @property
    def available(self) -> bool:
        """Unavailable once the room is gone."""
        return super().available and self.target is not None
