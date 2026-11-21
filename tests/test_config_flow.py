"""Tests for the config and reconfigure flow. No test touches a network."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_IGNORE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant.common import MockConfigEntry, mock_component

from custom_components.twinkly_canvas.api import TwinklyConnectionError, TwinklyResponseError
from custom_components.twinkly_canvas.const import DOMAIN

from .fake_device import GESTALT

HOST = "192.0.2.10"
NEW_HOST = "192.0.2.11"
MAC = "aa:bb:cc:00:11:22"
CLIENT = "custom_components.twinkly_canvas.config_flow.TwinklyClient"


@pytest.fixture(autouse=True)
def _custom_integrations(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Let Home Assistant find this integration; its dependencies count as set up."""
    mock_component(hass, "frontend")
    mock_component(hass, "http")


@pytest.fixture(autouse=True)
def _no_setup():
    """The flow tests do not set the entry up."""
    with patch(
        "custom_components.twinkly_canvas.async_setup_entry", return_value=True, create=True
    ) as setup:
        yield setup


async def _start(hass: HomeAssistant, gestalt=None, firmware="2.8.10", error=None):
    """Run the user step with host HOST against a patched device."""
    reply = {**GESTALT, **(gestalt or {})}
    with (
        patch(f"{CLIENT}.gestalt", return_value=reply, side_effect=error),
        patch(f"{CLIENT}.firmware_version", return_value=firmware),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": HOST}
        )


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Tree"
    assert result["data"] == {"host": HOST}
    assert result["result"].unique_id == MAC


@pytest.mark.parametrize(
    "error",
    [TwinklyConnectionError("down"), TwinklyResponseError(404, "not found")],
)
async def test_cannot_connect(hass: HomeAssistant, error: Exception) -> None:
    result = await _start(hass, error=error)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unsupported_firmware(hass: HomeAssistant) -> None:
    result = await _start(hass, firmware="2.4.0")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_device"


async def test_unsupported_profile(hass: HomeAssistant) -> None:
    result = await _start(hass, gestalt={"led_profile": "AWW"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_device"


async def test_missing_led_info(hass: HomeAssistant) -> None:
    gestalt = {k: v for k, v in GESTALT.items() if k not in ("led_profile", "bytes_per_led")}
    with (
        patch(f"{CLIENT}.gestalt", return_value=gestalt),
        patch(f"{CLIENT}.firmware_version", return_value="2.8.10"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": HOST}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_device"


async def test_compatibility_mode_off(hass: HomeAssistant) -> None:
    result = await _start(hass, gestalt={"compatibility_mode": "disabled"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "compatibility_mode_off"


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={"host": HOST}).add_to_hass(hass)
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_core_twinkly_guard(hass: HomeAssistant) -> None:
    MockConfigEntry(domain="twinkly", unique_id=MAC).add_to_hass(hass)
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "core_twinkly_configured"


async def test_ignored_core_twinkly_does_not_block(hass: HomeAssistant) -> None:
    MockConfigEntry(domain="twinkly", unique_id=MAC, source=SOURCE_IGNORE).add_to_hass(hass)
    result = await _start(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_old_rgb_gestalt_creates_entry(hass: HomeAssistant) -> None:
    gestalt = {k: v for k, v in GESTALT.items() if k != "led_profile"}
    with (
        patch(f"{CLIENT}.gestalt", return_value={**gestalt, "bytes_per_led": 3}),
        patch(f"{CLIENT}.firmware_version", return_value="2.6.0"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": HOST}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _reconfigure(hass: HomeAssistant, gestalt=None):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, title="Test Tree", data={"host": HOST}
    )
    entry.add_to_hass(hass)
    reply = {**GESTALT, **(gestalt or {})}
    with (
        patch(f"{CLIENT}.gestalt", return_value=reply),
        patch(f"{CLIENT}.firmware_version", return_value="2.8.10"),
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": NEW_HOST}
        )
    return entry, result


async def test_reconfigure_changes_host(hass: HomeAssistant) -> None:
    entry, result = await _reconfigure(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"host": NEW_HOST}


async def test_reconfigure_wrong_device(hass: HomeAssistant) -> None:
    entry, result = await _reconfigure(hass, gestalt={"mac": "aa:bb:cc:00:11:33"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data == {"host": HOST}
