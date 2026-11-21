"""Actions; each one calls the light entity method async_<action>."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN
from .effects import EFFECT_KINDS

_SCHEMAS: dict[str, dict] = {
    "set_leds": {
        vol.Required("leds"): cv.string,
        vol.Optional("color"): cv.match_all,  # the light checks colours: invalid_color
        vol.Optional("color_name"): cv.string,
        vol.Optional("white"): cv.byte,
        vol.Optional("clear_others", default=False): cv.boolean,
    },
    "play_effect": {
        vol.Required("kind"): vol.In(EFFECT_KINDS),
        vol.Optional("colors", default=[]): vol.All(cv.ensure_list, vol.Length(max=3)),
        vol.Optional("leds", default="all"): cv.string,
        vol.Optional("speed", default=50): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional("keep_others", default=True): cv.boolean,
    },
    "apply_look": {vol.Required("look"): cv.string},
    "save_look": {vol.Required("name"): cv.string},
    "find_leds": {vol.Required("leds"): cv.string},
    "save_group": {vol.Required("name"): cv.string, vol.Required("leds"): cv.string},
    "delete_group": {vol.Required("name"): cv.string},
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions on the lights of this integration."""
    for name, schema in _SCHEMAS.items():
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=LIGHT_DOMAIN,
            schema=schema,
            func=f"async_{name}",
        )
