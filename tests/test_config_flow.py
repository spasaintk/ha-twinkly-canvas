"""Tests for the config and reconfigure flow. No test touches a network."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_USER,
    ConfigEntryDisabler,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import translation
from pytest_homeassistant.common import MockConfigEntry, mock_component

from custom_components.twinkly_canvas.api import (
    TwinklyConnectionError,
    TwinklyResponseError,
)
from custom_components.twinkly_canvas.const import DOMAIN, MIN_FIRMWARE

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
async def _no_setup(hass: HomeAssistant) -> AsyncGenerator[AsyncMock]:
    """The flow tests neither set up nor unload the entries they make."""
    with (
        patch(
            "custom_components.twinkly_canvas.async_setup_entry",
            AsyncMock(return_value=True),
        ) as setup,
        patch(
            "custom_components.twinkly_canvas.async_unload_entry",
            AsyncMock(return_value=True),
        ),
        # serving the card needs the real http component, which these tests mock
        patch("custom_components.twinkly_canvas.async_register_card", AsyncMock()),
    ):
        yield setup
        # Unload while mocked: the hass fixture unloads loaded entries only after this.
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await hass.config_entries.async_unload(entry.entry_id)


async def _start(hass: HomeAssistant, gestalt=None, firmware="2.8.10", error=None):
    """Run the user step with host HOST against a patched device; None leaves a key out."""
    reply = {k: v for k, v in {**GESTALT, **(gestalt or {})}.items() if v is not None}
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
    assert result["reason"] == "unsupported_firmware"
    assert result["description_placeholders"] == {"min_firmware": MIN_FIRMWARE}
    texts = await translation.async_get_translations(hass, "en", "config", [DOMAIN])
    assert (
        "{min_firmware}"
        in texts[f"component.{DOMAIN}.config.abort.unsupported_firmware"]
    )


@pytest.mark.parametrize(
    "gestalt",
    [
        {"led_profile": "AWW"},  # Gold strings: no colour LEDs
        {"bytes_per_led": None},  # LED information missing
        {"led_profile": None, "bytes_per_led": 4},
        {"number_of_led": 0},
    ],
)
async def test_unsupported_device(hass: HomeAssistant, gestalt: dict) -> None:
    result = await _start(hass, gestalt=gestalt)
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
    MockConfigEntry(domain="twinkly", unique_id="AA-BB-CC-00-11-22").add_to_hass(hass)
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "core_twinkly_configured"


@pytest.mark.parametrize(
    "core",
    [{"source": SOURCE_IGNORE}, {"disabled_by": ConfigEntryDisabler.USER}],
)
async def test_inactive_core_twinkly_does_not_block(
    hass: HomeAssistant, core: dict
) -> None:
    MockConfigEntry(domain="twinkly", unique_id=MAC, **core).add_to_hass(hass)
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
    await hass.async_block_till_done()  # the reload a reconfigure starts
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
