"""Serve the canvas card and load it on every dashboard."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from ..const import DOMAIN

URL_BASE = "/twinkly_canvas"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve this folder and add the card's script to the frontend."""
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(Path(__file__).parent), cache_headers=False)]
    )
    # the version in the URL makes browsers fetch the card again after an update
    frontend.add_extra_js_url(hass, f"{URL_BASE}/twinkly-canvas-card.js?v={version}")
