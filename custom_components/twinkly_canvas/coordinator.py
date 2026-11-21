"""Polls one device and holds what a loaded entry keeps."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TwinklyClient, TwinklyError
from .const import DOMAIN, POLL_INTERVAL
from .device import DeviceInfoData, DeviceState, TwinklyDevice
from .store import CanvasStore

_LOGGER = logging.getLogger(__name__)

type TwinklyCanvasConfigEntry = ConfigEntry[TwinklyCanvasData]


@dataclass
class TwinklyCanvasData:
    """The runtime data of one entry."""

    client: TwinklyClient
    device: TwinklyDevice
    coordinator: TwinklyCanvasCoordinator
    store: CanvasStore
    info: DeviceInfoData


class TwinklyCanvasCoordinator(DataUpdateCoordinator[DeviceState]):
    """Polls mode, brightness, the playing effect or movie and the movie list."""

    config_entry: TwinklyCanvasConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TwinklyCanvasConfigEntry,
        device: TwinklyDevice,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=POLL_INTERVAL,
        )
        self.device = device

    async def _async_update_data(self) -> DeviceState:
        try:
            return await self.device.async_get_state()
        except TwinklyError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
