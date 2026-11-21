"""The Twinkly Canvas integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TwinklyClient, TwinklyError
from .const import CONF_HOST, DOMAIN, PLATFORMS
from .coordinator import (
    TwinklyCanvasConfigEntry,
    TwinklyCanvasCoordinator,
    TwinklyCanvasData,
)
from .device import DeviceInfoData, TwinklyDevice
from .store import CanvasStore

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the parts every entry shares."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TwinklyCanvasConfigEntry) -> bool:
    """Connect to the device, load its canvas and set up the light."""
    # First: the token fight with core twinkly can be what makes the rest fail.
    _update_core_twinkly_issue(hass, entry)
    client = TwinklyClient(entry.data[CONF_HOST], async_get_clientsession(hass))
    try:
        info = DeviceInfoData.from_api(
            await client.gestalt(), await client.firmware_version(), await client.led_config()
        )
    except TwinklyError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="update_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    store = CanvasStore(hass, entry.entry_id, info.led_count)
    await store.async_load()
    device = TwinklyDevice(hass, client, store, info)
    coordinator = TwinklyCanvasCoordinator(hass, entry, device)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = TwinklyCanvasData(client, device, coordinator, store, info)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TwinklyCanvasConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    await entry.runtime_data.device.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: TwinklyCanvasConfigEntry) -> None:
    """Delete the stored canvas, groups and looks."""
    # shortcut: a save still pending from the last second of use can write the file
    # again; flush the store on unload if that ever matters.
    await CanvasStore(hass, entry.entry_id, 0).async_remove()


def _update_core_twinkly_issue(hass: HomeAssistant, entry: TwinklyCanvasConfigEntry) -> None:
    """Raise a repair issue while core twinkly has an entry for the same device."""
    issue_id = f"core_twinkly_conflict_{entry.entry_id}"
    if any(
        e.unique_id == entry.unique_id
        for e in hass.config_entries.async_entries("twinkly", include_ignore=False)
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            config_entry_id=entry.entry_id,  # removing this entry removes the issue
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="core_twinkly_conflict",
            translation_placeholders={"name": entry.title},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
