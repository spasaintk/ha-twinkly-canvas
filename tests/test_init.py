"""Tests for setup, unload, removal and the core twinkly repair issue."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from pytest_homeassistant.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
)

from custom_components.twinkly_canvas.canvas import Canvas
from custom_components.twinkly_canvas.const import DOMAIN
from custom_components.twinkly_canvas.store import Active, CanvasStore

from .fake_device import GESTALT, FakeTwinkly, advance

MAC = GESTALT["mac"]


async def test_setup_and_unload(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("light.test_tree") is not None
    assert entity_registry.async_get("light.test_tree").unique_id == f"{MAC}_light"
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, MAC), entry.entry_id
    )
    assert device.connections == {(dr.CONNECTION_NETWORK_MAC, MAC)}
    assert (device.manufacturer, device.model, device.name) == (
        "Twinkly",
        "TEST400",
        "Test Tree",
    )
    assert (device.sw_version, device.hw_version) == ("2.8.18", "100")

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_unload_cancels_pending_commit(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    device = entry.runtime_data.device
    canvas = Canvas(400)
    canvas.set(range(5), (255, 0, 0, 0))
    entry.runtime_data.store.set_canvas(canvas)  # the card stores the painting first
    await device.async_request_preview()
    movies = len(fake.movies)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await advance(hass, 10)
    assert len(fake.movies) == movies
    assert fake.rt_frames == []  # the debounced preview frame never went out either


async def test_unload_saves_store_at_once(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    entry.runtime_data.store.save_group("Top", "1-10")
    assert await hass.config_entries.async_unload(
        entry.entry_id
    )  # within the 1 s delay
    store = CanvasStore(hass, entry.entry_id, 400)
    await store.async_load()
    assert store.groups == {"Top": "1-10"}


async def _paint_live(entry: MockConfigEntry) -> Canvas:
    """Paint like the card: store the canvas, then ask for its live preview."""
    canvas = Canvas(400)
    canvas.set(range(5), (255, 0, 0, 0))
    entry.runtime_data.store.set_canvas(canvas)
    await entry.runtime_data.device.async_request_preview()
    return canvas


async def test_stop_stores_pending_painting(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    canvas = await _paint_live(entry)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert fake.playing.data == canvas.to_frame("RGBW")
    calls = len(fake.calls)
    await advance(hass, 5)
    assert fake.calls[calls:] == []  # no live frame, no upload


async def test_stop_drops_painting_it_cannot_store(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly, hass_storage: dict
) -> None:
    """The painting stays active without a movie: the next start uploads it."""
    device = entry.runtime_data.device
    await device.async_show_canvas(Canvas(400), Active("painted", None))
    await device.async_select_builtin(1)
    await _paint_live(entry)
    fake.fail_upload = True
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]  # written at once
    assert stored["active"] == {"type": "painted", "ref": None}
    assert stored["current_movie_uid"] is None
    calls = len(fake.calls)
    await advance(hass, 5)
    assert fake.calls[calls:] == []


async def test_setup_retries_when_unreachable(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.fail_with_connection_error = True
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize(
    "gestalt",
    [
        {k: v for k, v in GESTALT.items() if k != "number_of_led"},  # KeyError
        {**GESTALT, "mac": None},  # TypeError
    ],
)
async def test_setup_fails_on_unreadable_gestalt(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake: FakeTwinkly, gestalt: dict
) -> None:
    fake.gestalt = AsyncMock(return_value=gestalt)  # type: ignore[method-assign]
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert config_entry.error_reason_translation_key == "unsupported_device"
    assert config_entry.reason.startswith("Twinkly Canvas cannot read")


async def test_core_twinkly_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake: FakeTwinkly,
    issue_registry: ir.IssueRegistry,
) -> None:
    core = MockConfigEntry(
        domain="twinkly", unique_id="AA-BB-CC-00-11-22"
    )  # same device
    core.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    issue_id = f"core_twinkly_conflict_{config_entry.entry_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "core_twinkly_conflict"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert not issue.is_fixable

    # The user removes the core entry and reloads this one: the issue goes.
    mock_integration(hass, MockModule("twinkly"))  # core twinkly itself needs ttls
    await hass.config_entries.async_remove(core.entry_id)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_disabled_core_twinkly_raises_no_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake: FakeTwinkly,
    issue_registry: ir.IssueRegistry,
) -> None:
    MockConfigEntry(
        domain="twinkly", unique_id=MAC, disabled_by=ConfigEntryDisabler.USER
    ).add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    issue_id = f"core_twinkly_conflict_{config_entry.entry_id}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_remove_entry_deletes_store(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict
) -> None:
    entry.runtime_data.store.save_group("Top", "1-10")
    await advance(hass, 2)  # the store saves 1 s after a change
    key = f"{DOMAIN}.{entry.entry_id}"
    assert hass_storage[key]["data"]["groups"] == {"Top": "1-10"}

    await hass.config_entries.async_remove(entry.entry_id)
    assert key not in hass_storage
