"""Diagnostics for the Advanced UniFi Access Directory integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import AccessConfigEntry

TO_REDACT = {
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    "email",
    "user_email",
    "phone_number",
    "avatar",
    "ip",
    "mac",
    "name",
    "display_name",
    "full_name",
    "alias",
    "answered_by",
    "target_name",
    "thumbnail_url",
    "video_url",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AccessConfigEntry
) -> dict[str, Any]:
    """Return config entry diagnostics.

    Includes the raw topology so the integration can be adapted when Access
    changes its internal API.
    """
    coordinator = entry.runtime_data
    data = coordinator.data
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "callers": {cid: asdict(c) for cid, c in data.callers.items()},
            # Prefixed so "phone_number" is not caught by the redaction.
            "catalog_counts": {f"type_{k}": len(v) for k, v in data.catalog.items()},
            "raw_topology": coordinator.raw_topology,
            "calls": (
                {
                    "websocket_connected": coordinator.calls.ws_connected,
                    "last_call": {
                        cid: call.as_dict() for cid, call in coordinator.calls.last_call.items()
                    },
                    "recent_log_entries": coordinator.calls.recent,
                }
                if coordinator.calls
                else None
            ),
        },
        TO_REDACT,
    )
