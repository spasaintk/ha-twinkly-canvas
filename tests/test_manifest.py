"""Test that Home Assistant can load the integration manifest."""

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.twinkly_canvas.const import DOMAIN


async def test_manifest_loads(hass: HomeAssistant) -> None:
    """The loader finds the integration with its domain, version and config flow."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == "twinkly_canvas"
    assert integration.version == "0.1.1"
    assert integration.config_flow is True
