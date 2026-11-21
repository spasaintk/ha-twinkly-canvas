"""Tests for the light entity, run against the simulated device."""

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
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.twinkly_canvas.canvas import Canvas
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


async def test_off_on_keeps_playlist(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.mode = "playlist"  # chosen in the app
    await _turn_off(hass)
    await _turn_on(hass)
    assert fake.mode == "playlist"


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


async def test_unavailable_when_unreachable(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.fail_with_connection_error = True
    await advance(hass, 61)
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE

    fake.fail_with_connection_error = False
    await advance(hass, 61)
    assert hass.states.get(ENTITY).state == STATE_ON
