"""Tests for the redacted diagnostics."""

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.twinkly_canvas.diagnostics import async_get_config_entry_diagnostics

from .fake_device import FakeTwinkly


async def test_diagnostics_redacts(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag)
    for secret in ("192.0.2.10", "aa:bb:cc:00:11:22", "Test Tree", *(m.name for m in fake.movies)):
        assert secret not in text
    assert '"led_count": 400' in text
    assert set(diag["store"]) == {"groups", "looks", "our_movies", "stranded"}
