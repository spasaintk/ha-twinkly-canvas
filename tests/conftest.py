"""Shared fixtures: a simulated device and an entry set up against it."""

from collections.abc import Generator
from unittest.mock import patch

import pytest
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry, mock_component

from custom_components.twinkly_canvas.const import DOMAIN

from .fake_device import GESTALT, FakeTwinkly


@pytest.fixture
def fake(request: pytest.FixtureRequest) -> Generator[FakeTwinkly]:
    """The simulated device; setup gets it in place of a TwinklyClient.

    Parametrize it indirectly with "RGB" for lights without a white channel.
    """
    fake = FakeTwinkly(getattr(request, "param", "RGBW"))
    with patch("custom_components.twinkly_canvas.TwinklyClient", return_value=fake):
        yield fake


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """An entry for the simulated device, not set up yet."""
    mock_component(hass, "frontend")  # needs hass_frontend; http is set up for real
    # what the real frontend setup prepares for add_extra_js_url
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=GESTALT["mac"],
        title="Test Tree",
        data={"host": "192.0.2.10"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake: FakeTwinkly
) -> MockConfigEntry:
    """The entry, set up against the simulated device."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
