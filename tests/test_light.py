"""Tests for the light entity, run against the simulated device."""

import asyncio

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from custom_components.twinkly_canvas.canvas import Canvas
from custom_components.twinkly_canvas.const import DOMAIN
from custom_components.twinkly_canvas.effects import EffectSpec
from custom_components.twinkly_canvas.store import Look

from .fake_device import FakeTwinkly, advance

ENTITY = "light.test_tree"
BUILTINS = [f"Built-in {n}" for n in range(1, 6)]
APPS = [f"App: App movie {n}" for n in range(1, 8)]


async def _turn_on(hass: HomeAssistant, **data) -> None:
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY, **data}, blocking=True
    )


async def _turn_off(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )


def _attr(hass: HomeAssistant, name: str):
    return hass.states.get(ENTITY).attributes[name]


async def test_light_state(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    state = hass.states.get(ENTITY)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 255  # device brightness 100
    assert state.attributes[ATTR_EFFECT_LIST] == ["Painted", *BUILTINS, *APPS]
    assert state.attributes[ATTR_EFFECT] == "Built-in 1"
    # HA accepts BRIGHTNESS (or ONOFF) besides the supported modes while an effect runs
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.BRIGHTNESS


async def test_color_mode_without_effect(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.mode = "color"  # set by the app: not an effect list entry
    await entry.runtime_data.coordinator.async_refresh()
    assert _attr(hass, ATTR_EFFECT) is None
    # without an effect HA accepts only the supported modes
    assert _attr(hass, ATTR_COLOR_MODE) == ColorMode.RGBW


async def test_effect_list_order(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    store = entry.runtime_data.store
    store.save_look(Look("Tinsel", Canvas(400), None))
    store.save_look(Look("angel", Canvas(400), None))
    store.set_custom_effect(EffectSpec("chase", tuple(range(20))))
    fake.add_app_movie("App movie 1", 10)
    fake.add_app_movie("App movie 1", 10)
    await entry.runtime_data.coordinator.async_refresh()
    assert _attr(hass, ATTR_EFFECT_LIST) == [
        "Painted",
        "Custom effect",
        "Look: angel",
        "Look: Tinsel",
        *BUILTINS,
        *APPS,
        "App: App movie 1 (2)",
        "App: App movie 1 (3)",
    ]


async def test_turn_on_rgbw_fills_canvas(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    await _turn_on(hass, rgbw_color=(10, 20, 30, 40))
    assert fake.playing.data == bytes([40, 10, 20, 30]) * 400
    assert _attr(hass, ATTR_EFFECT) == "Painted"
    assert _attr(hass, ATTR_COLOR_MODE) == ColorMode.RGBW
    assert _attr(hass, ATTR_RGBW_COLOR) == (10, 20, 30, 40)
    assert "App: " + fake.playing.name not in _attr(hass, ATTR_EFFECT_LIST)


@pytest.mark.parametrize("fake", ["RGB"], indirect=True)
async def test_rgb_lights(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    """Lights without a white channel: colours are RGB, white folds into warm white."""
    store = entry.runtime_data.store
    assert _attr(hass, ATTR_SUPPORTED_COLOR_MODES) == [ColorMode.RGB]
    await _turn_on(hass, rgb_color=(10, 20, 30))
    assert store.canvas.pixels == [(10, 20, 30, 0)] * 400
    assert fake.playing.data == bytes([10, 20, 30]) * 400
    assert _attr(hass, ATTR_COLOR_MODE) == ColorMode.RGB
    assert _attr(hass, ATTR_RGB_COLOR) == (10, 20, 30)

    store.set_canvas(Canvas(400, [(0, 0, 0, 255)] * 400))  # warm white
    entry.runtime_data.coordinator.async_update_listeners()
    assert _attr(hass, ATTR_RGB_COLOR) == (255, 180, 107)
    store.set_canvas(Canvas(400, [(0, 0, 0, 255)] * 399 + [(0, 0, 0, 0)]))
    entry.runtime_data.coordinator.async_update_listeners()
    assert _attr(hass, ATTR_RGB_COLOR) is None


async def test_turn_on_effect_builtin(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    await _turn_on(hass, effect="Built-in 3")
    assert (fake.mode, fake.effect_id) == ("effect", 2)
    assert _attr(hass, ATTR_EFFECT) == "Built-in 3"


async def test_turn_on_effect_look(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    canvas = Canvas(400)
    canvas.set(range(10), (255, 215, 0, 0))
    entry.runtime_data.store.save_look(Look("Gold", canvas, None))
    await _turn_on(hass, effect="Look: Gold")
    assert fake.playing.data == canvas.to_frame("RGBW")
    assert _attr(hass, ATTR_EFFECT) == "Look: Gold"


async def test_turn_on_effect_app_movie(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    await _turn_on(hass, effect="App: App movie 3")
    assert fake.playing is fake.movies[2]
    assert _attr(hass, ATTR_EFFECT) == "App: App movie 3"


async def test_turn_on_effect_custom_then_painted(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    entry.runtime_data.store.set_custom_effect(EffectSpec("chase", tuple(range(20))))
    await _turn_on(hass, effect="Custom effect")
    assert fake.playing.frames > 1
    assert _attr(hass, ATTR_EFFECT) == "Custom effect"
    await _turn_on(hass, effect="Painted")
    assert fake.playing.frames == 1
    assert fake.playing.data == bytes(1600)  # the canvas: all off


async def test_turn_on_unknown_effect(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _turn_on(hass, effect="Look: Missing")
    assert err.value.translation_key == "unknown_effect"


async def test_turn_on_validates_effect_first(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _turn_on(hass, brightness=128, effect="Look: Missing")
    assert err.value.translation_key == "unknown_effect"
    assert fake.brightness == 100  # refused before any change


async def test_turn_off(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    await _turn_off(hass)
    assert fake.mode == "off"
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_off_on_keeps_app_effect(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.effect_id = 2  # chosen in the app
    await _turn_off(hass)
    await _turn_on(hass)
    assert (fake.mode, fake.effect_id) == ("effect", 2)
    assert len(fake.movies) == 7  # nothing uploaded


async def test_off_on_keeps_app_movie(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.current, fake.mode = fake.movies[3], "movie"  # chosen in the app
    await _turn_off(hass)
    await _turn_on(hass)
    assert fake.playing is fake.movies[3]
    assert len(fake.movies) == 7  # nothing uploaded


@pytest.mark.parametrize("mode", ["playlist", "color", "demo"])
async def test_off_on_keeps_device_mode(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly, mode: str
) -> None:
    fake.mode = mode  # chosen in the app
    await _turn_off(hass)
    await _turn_on(hass)
    assert fake.mode == mode


async def test_turn_on_while_on_changes_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.effect_id = 2  # chosen in the app
    calls = len(fake.calls)
    changes = {"set_mode", "set_effect", "new_movie", "send_rt_frame"}
    await _turn_on(hass)
    assert (fake.mode, fake.effect_id, len(fake.movies)) == ("effect", 2, 7)
    assert not changes & set(fake.calls[calls:])

    await _turn_on(hass, brightness=128)
    assert fake.brightness == 50
    assert (fake.mode, fake.effect_id, len(fake.movies)) == ("effect", 2, 7)
    assert not changes & set(fake.calls[calls:])


async def test_brightness(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    await _turn_on(hass, brightness=128)
    assert fake.brightness == 50
    assert (fake.mode, fake.effect_id) == ("effect", 0)  # what plays keeps playing
    assert _attr(hass, ATTR_BRIGHTNESS) == 128

    await _turn_on(hass, brightness=0)  # HA's light service turns 0 into turn_off
    assert fake.mode == "off"


async def test_brightness_turns_on_when_off(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    await _turn_off(hass)
    await _turn_on(hass, brightness=128)
    assert fake.brightness == 50
    assert fake.mode != "off"


async def test_brightness_turns_on_within_refresh_cooldown(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    """Brightness alone turns on lights that are off, even before HA has seen the off."""
    await _turn_on(hass, effect="Built-in 2")  # refreshed: the next refresh waits 10 s
    await _turn_off(hass)
    await _turn_on(hass, brightness=128)
    assert (fake.mode, fake.effect_id, fake.brightness) == ("effect", 1, 50)


async def test_scene_restores_painting(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A snapshot of a painting brings it back: HA turns on effect "Painted"."""
    assert await async_setup_component(hass, "scene", {})
    await hass.async_block_till_done()  # scene.create comes with a later platform
    await hass.services.async_call(
        DOMAIN,
        "set_leds",
        {ATTR_ENTITY_ID: ENTITY, "leds": "1-10", "color_name": "red"},
        blocking=True,
    )
    assert _attr(hass, ATTR_EFFECT) == "Painted"
    assert _attr(hass, ATTR_COLOR_MODE) == ColorMode.BRIGHTNESS  # not one colour
    await hass.services.async_call(
        "scene",
        "create",
        {"scene_id": "before", "snapshot_entities": [ENTITY]},
        blocking=True,
    )
    await _turn_on(hass, effect="Built-in 3")
    await advance(hass, 10)  # the refresh after a refresh waits for its cooldown
    assert fake.mode == "effect"

    await hass.services.async_call(
        "scene", "turn_on", {ATTR_ENTITY_ID: "scene.before"}, blocking=True
    )
    red, off = bytes([0, 255, 0, 0]), bytes(4)  # W, R, G, B
    assert fake.playing.data == red * 10 + off * 390
    assert not [r for r in caplog.records if r.name.endswith("reproduce_state")]


async def test_live_preview_reports_painted(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    data = entry.runtime_data
    canvas = Canvas(400)
    canvas.set(range(10), (255, 0, 0, 0))
    data.store.set_canvas(canvas)  # the card stores the painting, then previews it
    await data.device.async_request_preview()
    await advance(hass, 0.6)
    await data.coordinator.async_refresh()
    assert fake.mode == "rt"
    assert _attr(hass, ATTR_EFFECT) == "Painted"
    assert _attr(hass, ATTR_COLOR_MODE) == ColorMode.BRIGHTNESS


async def test_color_keeps_unsaved_painting(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    store = entry.runtime_data.store
    for leds, color in (("1-10", "red"), ("11-20", "gold")):
        await hass.services.async_call(
            DOMAIN,
            "set_leds",
            {ATTR_ENTITY_ID: ENTITY, "leds": leds, "color_name": color},
            blocking=True,
        )
    assert store.looks == {}  # paint edits never keep the painting they change
    painting = store.canvas.copy()
    await _turn_on(hass, rgbw_color=(0, 0, 0, 255))
    assert store.looks["Previous painting"].canvas.pixels == painting.pixels
    await _turn_on(hass, effect="Look: Previous painting")
    assert fake.playing.data == painting.to_frame("RGBW")


async def test_color_over_one_colour_keeps_nothing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await _turn_on(hass, rgbw_color=(0, 0, 0, 255))  # over the dark canvas
    await _turn_on(hass, rgb_color=(255, 0, 0))  # over warm white
    assert entry.runtime_data.store.looks == {}


async def test_painted_shows_strokes_made_while_waiting(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    """Picking "Painted" shows the canvas as it is once the device is free."""
    data = entry.runtime_data
    lookup, get_movies = asyncio.Event(), fake.get_movies

    async def slow_get_movies() -> dict:
        await lookup.wait()
        return await get_movies()

    fake.get_movies = slow_get_movies  # type: ignore[method-assign]
    # selecting an app movie holds the lock while it waits for the movie list
    select = hass.async_create_task(data.device.async_select_app_movie(3))
    painted = hass.async_create_task(_turn_on(hass, effect="Painted"))
    for _ in range(100):  # until "Painted" waits for the lock
        if data.device._lock._waiters:
            break
        await asyncio.sleep(0)
    assert data.device._lock._waiters
    canvas = data.store.canvas.copy()
    canvas.set([0], (255, 0, 0, 0))
    data.store.set_canvas(canvas)  # a stroke, painted with live off
    lookup.set()
    await select
    await painted
    assert data.store.canvas.pixels[0] == (255, 0, 0, 0)
    assert fake.playing.data == data.store.canvas.to_frame("RGBW")


async def test_unavailable_when_unreachable(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.fail_with_connection_error = True
    await advance(hass, 61)
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE

    fake.fail_with_connection_error = False
    await advance(hass, 61)
    assert hass.states.get(ENTITY).state == STATE_ON
