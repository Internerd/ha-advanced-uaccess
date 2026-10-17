"""Advanced UniFi Access Directory integration.

Manages the intercom directory (rooms and their receivers) of UniFi Access
through a local connection to the UniFi OS console.
"""

from __future__ import annotations

import aiohttp
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import AccessClient
from .const import CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL, DIRECT_CALL, DOMAIN
from .coordinator import AccessConfigEntry, AccessCoordinator
from .entity import caller_device_info
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services."""
    async_setup_services(hass)
    return True


def create_client(hass: HomeAssistant, data: dict) -> AccessClient:
    """Create a client with its own cookie jar (UniFi OS uses a session cookie)."""
    verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_create_clientsession(
        hass,
        verify_ssl=verify_ssl,
        # unsafe=True is required to keep cookies for plain IP hosts.
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    return AccessClient(
        session,
        data[CONF_HOST],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        verify_ssl=verify_ssl,
    )


async def async_setup_entry(hass: HomeAssistant, entry: AccessConfigEntry) -> bool:
    """Set up a console from a config entry."""
    coordinator = AccessCoordinator(hass, entry, create_client(hass, dict(entry.data)))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    registry = dr.async_get(hass)
    for caller in coordinator.data.callers.values():
        registry.async_get_or_create(config_entry_id=entry.entry_id, **caller_device_info(caller))

    @callback
    def _remove_stale_devices() -> None:
        """Drop devices of rooms or intercoms that no longer exist."""
        valid: set[str] = set()
        for caller in coordinator.data.callers.values():
            valid.add(caller.id)
            valid.add(f"{caller.id}_{DIRECT_CALL}")
            valid.update(f"{caller.id}_{room_id}" for room_id in caller.rooms)
        registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            ids = {i[1] for i in device.identifiers if i[0] == DOMAIN}
            if ids and not ids & valid:
                registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    entry.async_on_unload(coordinator.async_add_listener(_remove_stale_devices))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: AccessConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AccessConfigEntry) -> bool:
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.close()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AccessConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing devices that are no longer part of the directory."""
    data = entry.runtime_data.data
    for caller in data.callers.values():
        valid = {caller.id, f"{caller.id}_{DIRECT_CALL}"}
        valid.update(f"{caller.id}_{room_id}" for room_id in caller.rooms)
        if any(i[0] == DOMAIN and i[1] in valid for i in device.identifiers):
            return False
    return True
