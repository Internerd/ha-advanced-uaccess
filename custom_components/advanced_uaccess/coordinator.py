"""Data coordinator for the Advanced UniFi Access Directory integration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AccessApiError,
    AccessAuthError,
    AccessClient,
    AccessError,
    Caller,
    Receiver,
    Target,
    extract_week_schedule,
    parse_topology,
)
from .const import (
    CONF_RESTORE_SCHEDULES,
    CONF_SCAN_INTERVAL,
    DEFAULT_RESTORE_SCHEDULES,
    DEFAULT_SCAN_INTERVAL,
    DIRECT_CALL,
    DOMAIN,
    RECEIVER_TYPES,
    STORAGE_VERSION,
    TYPE_ADMIN,
    TYPE_CHIME,
    TYPE_PHONE,
    TYPE_VIEWER,
)

_LOGGER = logging.getLogger(__name__)

CATALOG_MAX_AGE = 300

type AccessConfigEntry = ConfigEntry[AccessCoordinator]


@dataclass(slots=True)
class AccessData:
    """Snapshot of all intercoms and the selectable receivers."""

    callers: dict[str, Caller] = field(default_factory=dict)
    # receiver type -> {receiver id: display name}
    catalog: dict[str, dict[str, str]] = field(default_factory=dict)

    def receiver_name(self, receiver_type: str, receiver_id: str) -> str:
        """Return the best known display name of a receiver."""
        return self.catalog.get(receiver_type, {}).get(receiver_id, "")


class AccessCoordinator(DataUpdateCoordinator[AccessData]):
    """Polls the directory topology of all intercoms on one console."""

    config_entry: AccessConfigEntry

    def __init__(self, hass: HomeAssistant, entry: AccessConfigEntry, client: AccessClient) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.raw_topology: dict[str, Any] = {}
        # caller id -> device registry id of the intercom device
        self.caller_device_ids: dict[str, str] = {}
        self._catalog: dict[str, dict[str, str]] = {}
        self._catalog_time: float | None = None
        self._write_lock = asyncio.Lock()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._schedules: dict[str, dict[str, Any]] = {}

    @property
    def restore_schedules(self) -> bool:
        """Return True if private schedules are backed up and restored."""
        return self.config_entry.options.get(CONF_RESTORE_SCHEDULES, DEFAULT_RESTORE_SCHEDULES)

    async def _async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self._schedules = dict(stored.get("schedules") or {})

    async def _async_update_data(self) -> AccessData:
        try:
            if (
                self._catalog_time is None
                or time.monotonic() - self._catalog_time > CATALOG_MAX_AGE
            ):
                await self._refresh_catalog()
            callers: dict[str, Caller] = {}
            for info in await self.client.get_callers():
                caller_id = info.get("unique_id")
                if not caller_id:
                    continue
                callers[caller_id] = await self._load_caller(caller_id, info)
        except AccessAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except AccessError as err:
            raise UpdateFailed(str(err)) from err
        return AccessData(callers=callers, catalog=self._catalog)

    async def _refresh_catalog(self) -> None:
        catalog: dict[str, dict[str, str]] = {}
        for receiver_type, loader in (
            (TYPE_VIEWER, self.client.get_viewers),
            (TYPE_CHIME, self.client.get_chimes),
            (TYPE_ADMIN, self.client.get_users),
            (TYPE_PHONE, self.client.get_phone_numbers),
        ):
            try:
                catalog[receiver_type] = await loader()
            except AccessAuthError:
                raise
            except AccessError as err:
                # Keep the previous list; one failing endpoint must not break polling.
                _LOGGER.warning("Could not load %s list: %s", receiver_type, err)
                catalog[receiver_type] = self._catalog.get(receiver_type, {})
        self._catalog = catalog
        self._catalog_time = time.monotonic()

    async def _load_caller(self, caller_id: str, info: dict[str, Any]) -> Caller:
        topology = await self.client.get_topology(caller_id)
        self.raw_topology[caller_id] = topology
        rooms, direct, extra = parse_topology(caller_id, topology)
        for target in (direct, *rooms.values()):
            for receiver in target.receivers:
                if known := self._catalog.get(receiver.type, {}).get(receiver.id):
                    receiver.name = known
        return Caller(
            id=caller_id,
            name=str(info.get("name") or extra.get("name") or caller_id),
            alias=str(info.get("alias") or ""),
            model=str(info.get("device_type") or extra.get("type") or ""),
            firmware=str(info.get("firmware") or ""),
            online=bool(info.get("is_online", True)),
            disable_directory=bool(extra.get("disable_directory", False)),
            rooms=rooms,
            direct=direct,
        )

    # ---- helpers --------------------------------------------------------

    def caller(self, caller_id: str | None) -> Caller:
        """Return a caller, or the only caller if none is given."""
        callers = self.data.callers if self.data else {}
        if caller_id is None:
            if len(callers) == 1:
                return next(iter(callers.values()))
            raise HomeAssistantError("caller_id is required when more than one intercom exists")
        if caller_id not in callers:
            raise HomeAssistantError(f"Unknown intercom {caller_id}")
        return callers[caller_id]

    async def _fresh_target(self, caller_id: str, target_id: str) -> Target:
        """Re-read the topology so list writes never use a stale state."""
        topology = await self.client.get_topology(caller_id)
        self.raw_topology[caller_id] = topology
        rooms, direct, _ = parse_topology(caller_id, topology)
        if target_id == DIRECT_CALL:
            return direct
        if target_id not in rooms:
            raise HomeAssistantError(f"Unknown room {target_id}")
        return rooms[target_id]

    async def _write_list(
        self, caller_id: str, target: Target, receivers: Iterable[Receiver]
    ) -> None:
        if target.is_direct:
            await self.client.set_direct_receivers(caller_id, receivers)
        else:
            await self.client.set_room_receivers(caller_id, target, receivers)

    @staticmethod
    def _schedule_key(caller_id: str, target_id: str, receiver_type: str, receiver_id: str) -> str:
        return f"{caller_id}|{target_id}|{receiver_type}|{receiver_id}"

    async def _save_store(self) -> None:
        await self._store.async_save({"schedules": self._schedules})

    # ---- write operations -----------------------------------------------

    async def async_add_receiver(
        self, caller_id: str, target_id: str, receiver_type: str, receiver_id: str
    ) -> None:
        """Add a receiver to a room or to the direct call."""
        if receiver_type not in RECEIVER_TYPES:
            raise HomeAssistantError(f"Unsupported receiver type {receiver_type}")
        async with self._write_lock:
            try:
                target = await self._fresh_target(caller_id, target_id)
                if target.find(receiver_type, receiver_id) is None:
                    await self._write_list(
                        caller_id,
                        target,
                        [*target.receivers, Receiver(receiver_type, receiver_id)],
                    )
            except AccessError as err:
                raise HomeAssistantError(f"Adding receiver failed: {err}") from err
            try:
                await self._restore_schedule(caller_id, target_id, receiver_type, receiver_id)
            except AccessError as err:
                # The receiver is attached; only the schedule is missing.
                _LOGGER.warning("Could not restore schedule of %s: %s", receiver_id, err)
        await self.async_refresh()

    async def async_remove_receiver(
        self, caller_id: str, target_id: str, receiver_type: str, receiver_id: str
    ) -> None:
        """Remove a receiver from a room or from the direct call."""
        async with self._write_lock:
            try:
                target = await self._fresh_target(caller_id, target_id)
                receiver = target.find(receiver_type, receiver_id)
                if receiver is None:
                    return
                await self._backup_schedule(caller_id, target_id, receiver)
                try:
                    await self.client.remove_receiver(
                        caller_id, target_id, receiver_type, receiver_id
                    )
                except AccessApiError as err:
                    # The granular DELETE is not verified for every type; the
                    # list replace is what the UI's "Manage" dialog does.
                    _LOGGER.warning(
                        "Granular removal of %s %s failed (%s), replacing list",
                        receiver_type,
                        receiver_id,
                        err,
                    )
                    await self._write_list(
                        caller_id,
                        target,
                        [r for r in target.receivers if r.key != receiver.key],
                    )
            except AccessError as err:
                raise HomeAssistantError(f"Removing receiver failed: {err}") from err
        await self.async_refresh()

    async def _backup_schedule(self, caller_id: str, target_id: str, receiver: Receiver) -> None:
        if not (self.restore_schedules and receiver.schedule_is_private and receiver.schedule_id):
            return
        try:
            schedule = await self.client.get_schedule(receiver.schedule_id)
        except AccessError as err:
            _LOGGER.warning("Could not back up schedule of %s: %s", receiver.id, err)
            return
        parsed = extract_week_schedule(schedule)
        if parsed is None:
            _LOGGER.warning(
                "Schedule %s has an unknown format, it will not be restored",
                receiver.schedule_id,
            )
            return
        week, holiday = parsed
        self._schedules[self._schedule_key(caller_id, target_id, receiver.type, receiver.id)] = {
            "week": week,
            "holiday": holiday,
            "saved": int(time.time()),
        }
        await self._save_store()

    async def _restore_schedule(
        self, caller_id: str, target_id: str, receiver_type: str, receiver_id: str
    ) -> None:
        key = self._schedule_key(caller_id, target_id, receiver_type, receiver_id)
        backup = self._schedules.get(key)
        if backup is None or not self.restore_schedules:
            return
        await self.client.set_schedule(
            caller_id,
            target_id,
            receiver_type,
            receiver_id,
            backup["week"],
            backup.get("holiday"),
            stamp=int(time.time() * 1000),
        )
        del self._schedules[key]
        await self._save_store()

    async def async_set_schedule(
        self,
        caller_id: str,
        target_id: str,
        receiver_type: str,
        receiver_id: str,
        week: dict[str, list[dict[str, str]]],
    ) -> None:
        """Set a custom schedule for an attached receiver."""
        async with self._write_lock:
            try:
                target = await self._fresh_target(caller_id, target_id)
                if target.find(receiver_type, receiver_id) is None:
                    raise HomeAssistantError(
                        "The receiver must be attached before it gets a schedule"
                    )
                await self.client.set_schedule(
                    caller_id,
                    target_id,
                    receiver_type,
                    receiver_id,
                    week,
                    stamp=int(time.time() * 1000),
                )
            except AccessError as err:
                raise HomeAssistantError(f"Setting schedule failed: {err}") from err
        await self.async_refresh()

    async def async_clear_schedule(
        self, caller_id: str, target_id: str, receiver_type: str, receiver_id: str
    ) -> None:
        """Reset a receiver's schedule to "always"."""
        async with self._write_lock:
            try:
                await self.client.clear_schedule(caller_id, target_id, receiver_type, receiver_id)
            except AccessError as err:
                raise HomeAssistantError(f"Clearing schedule failed: {err}") from err
            self._schedules.pop(
                self._schedule_key(caller_id, target_id, receiver_type, receiver_id),
                None,
            )
            await self._save_store()
        await self.async_refresh()

    async def async_create_room(
        self,
        caller_id: str,
        name: str,
        number: str,
        number_check: bool,
        receivers: list[Receiver],
    ) -> dict[str, Any]:
        """Create a room."""
        async with self._write_lock:
            try:
                room = await self.client.create_room(
                    caller_id, name, number, number_check, receivers
                )
            except AccessError as err:
                raise HomeAssistantError(f"Creating room failed: {err}") from err
        await self.async_refresh()
        return room

    async def async_delete_room(self, caller_id: str, room_id: str) -> None:
        """Delete a room."""
        async with self._write_lock:
            try:
                await self.client.delete_room(caller_id, room_id)
            except AccessError as err:
                raise HomeAssistantError(f"Deleting room failed: {err}") from err
            prefix = f"{caller_id}|{room_id}|"
            for key in [k for k in self._schedules if k.startswith(prefix)]:
                del self._schedules[key]
            await self._save_store()
        await self.async_refresh()
