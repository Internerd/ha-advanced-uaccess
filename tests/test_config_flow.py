"""Config flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.advanced_uaccess.api import (
    AccessApiError,
    AccessAuthError,
    AccessConnectionError,
)
from custom_components.advanced_uaccess.const import (
    CONF_RESTORE_SCHEDULES,
    CONF_SCAN_INTERVAL,
    CONF_USER_RECEIVERS,
    DOMAIN,
)

from .conftest import ENTRY_DATA, USER_BOB, FakeClient


async def test_user_flow(hass: HomeAssistant, fake_client: FakeClient) -> None:
    """A successful setup creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, "host": " 192.168.55.10/ "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "192.168.55.10"
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (AccessAuthError("x"), "invalid_auth"),
        (AccessConnectionError("x"), "cannot_connect"),
        (AccessApiError(0, "X", "y"), "no_access"),
        (RuntimeError("x"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, fake_client: FakeClient, error: Exception, key: str
) -> None:
    """Errors are shown in the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(fake_client, "login", AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"] == {"base": key}


async def test_no_intercom(hass: HomeAssistant, fake_client: FakeClient) -> None:
    """A console without intercom is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(fake_client, "get_callers", AsyncMock(return_value=[])):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"] == {"base": "no_intercom"}


async def test_duplicate(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """The same host cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Reauth stores the new password."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "ha-local", "password": "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["password"] == "new"


async def test_options(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    """Options are stored and offer the users of the console."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 60, CONF_RESTORE_SCHEDULES: False, CONF_USER_RECEIVERS: [USER_BOB]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_SCAN_INTERVAL] == 60
    await hass.async_block_till_done()
