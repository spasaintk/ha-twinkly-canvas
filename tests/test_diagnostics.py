"""Tests for the redacted diagnostics."""

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.twinkly_canvas.canvas import Canvas
from custom_components.twinkly_canvas.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.twinkly_canvas.store import Active, Look

from .fake_device import FakeTwinkly


async def test_diagnostics_redacts(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    data = entry.runtime_data
    data.store.save_group("Top", "1-10")
    data.store.save_look(Look("Gold", Canvas(400), None))
    # ours plays
    await data.device.async_show_canvas(Canvas(400), Active("painted", None))
    await data.coordinator.async_refresh()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag)
    secrets = (
        "192.0.2.10",
        "aa:bb:cc:00:11:22",
        "Test Tree",
        "Top",
        "Gold",
        *(m.name for m in fake.movies),
        *(m.unique_id for m in fake.movies),  # as listed: upper-cased
        *(m.unique_id.lower() for m in fake.movies),
    )
    for secret in secrets:
        assert secret not in text
    assert '"led_count": 400' in text
    assert diag["store"] == {"groups": 1, "looks": 1, "our_movies": 1, "stranded": 0}
    assert all(type(count) is int for count in diag["store"].values())
    assert diag["last_update_success"] is True
