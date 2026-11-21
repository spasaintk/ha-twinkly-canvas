"""Base entity: the device of one entry."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TwinklyCanvasCoordinator, TwinklyCanvasData


class TwinklyCanvasEntity(CoordinatorEntity[TwinklyCanvasCoordinator]):
    """An entity of one Twinkly device."""

    _attr_has_entity_name = True

    def __init__(self, data: TwinklyCanvasData) -> None:
        super().__init__(data.coordinator)
        self.device = data.device
        self.store = data.store
        info = data.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info.mac)},
            connections={(CONNECTION_NETWORK_MAC, info.mac)},
            manufacturer="Twinkly",
            model=info.model,
            name=info.name,
            sw_version=info.firmware,
            hw_version=info.hardware,
        )
