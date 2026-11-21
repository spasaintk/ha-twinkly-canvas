"""The integration serves the canvas card and adds it to every dashboard."""

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.twinkly_canvas.const import DOMAIN


async def test_card_js_served(
    hass: HomeAssistant, entry: MockConfigEntry, hass_client: ClientSessionGenerator
) -> None:
    client = await hass_client()
    response = await client.get("/twinkly_canvas/twinkly-canvas-card.js")
    assert response.status == 200
    assert 'customElements.define("twinkly-canvas-card"' in await response.text()
    for other in ("/twinkly_canvas/__init__.py", "/twinkly_canvas/"):  # only the card
        assert (await client.get(other)).status == 404

    version = (await async_get_integration(hass, DOMAIN)).version
    assert f"/twinkly_canvas/twinkly-canvas-card.js?v={version}" in (
        hass.data[DATA_EXTRA_MODULE_URL].urls
    )
