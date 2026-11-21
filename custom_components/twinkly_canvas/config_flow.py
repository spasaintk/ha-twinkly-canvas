"""Config and reconfigure flow: one field, the host."""

from __future__ import annotations

from typing import Any

from awesomeversion import AwesomeVersion
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from . import core_twinkly_configured
from .api import TwinklyClient, TwinklyError
from .const import CONF_HOST, DOMAIN, MIN_FIRMWARE
from .device import DeviceInfoData

_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class TwinklyCanvasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Twinkly device, or point an entry at a new host."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._step("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._step("reconfigure", user_input)

    async def _step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            client = TwinklyClient(host, async_get_clientsession(self.hass))
            try:
                gestalt = await client.gestalt()
                firmware = await client.firmware_version()
            except TwinklyError:  # a wrong host may answer 404, junk or 401, not only time out
                errors["base"] = "cannot_connect"
            else:
                # First: an older firmware may also be what leaves out the LED information.
                if AwesomeVersion(firmware) < AwesomeVersion(MIN_FIRMWARE):
                    return self.async_abort(
                        reason="unsupported_firmware",
                        description_placeholders={"min_firmware": MIN_FIRMWARE},
                    )
                try:
                    info = DeviceInfoData.from_api(gestalt, firmware, None)
                except (KeyError, TypeError):  # setup would fail on the same gestalt
                    return self.async_abort(reason="unsupported_device")
                if abort := self._check(gestalt, info):
                    return self.async_abort(reason=abort)
                unique_id = info.mac
                if step_id == "user":
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=info.name, data={CONF_HOST: host}
                    )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="unique_id_mismatch")
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(), data_updates={CONF_HOST: host}
                )
        schema = self.add_suggested_values_to_schema(
            _SCHEMA,
            user_input
            or (self._get_reconfigure_entry().data if step_id == "reconfigure" else {}),
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    def _check(self, gestalt: dict, info: DeviceInfoData) -> str | None:
        """The abort reason if this device cannot be used, else None."""
        # colors.to_wire supports only RGB and RGBW (not the Gold AWW strings)
        if info.profile not in ("RGB", "RGBW"):
            return "unsupported_device"
        if gestalt.get("compatibility_mode") == "disabled":
            return "compatibility_mode_off"
        if core_twinkly_configured(self.hass, info.mac):
            return "core_twinkly_configured"
        return None
