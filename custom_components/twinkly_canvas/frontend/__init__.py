"""Serve the canvas card and load it on every dashboard."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from ..const import DOMAIN

CARD = "twinkly-canvas-card.js"
CARD_URL = f"/twinkly_canvas/{CARD}"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card's script (nothing else of this folder) and add it to the frontend."""
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL, str(Path(__file__).parent / CARD), cache_headers=False
            )
        ]
    )
    # the version in the URL makes browsers fetch the card again after an update
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={version}")
