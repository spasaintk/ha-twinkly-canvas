"""Tests for the actions, run against the simulated device."""

import asyncio
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import icon, selector, translation
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml_dict
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.twinkly_canvas.canvas import Canvas
from custom_components.twinkly_canvas.colors import NAMED_COLORS
from custom_components.twinkly_canvas.const import DOMAIN
from custom_components.twinkly_canvas.effects import EFFECT_KINDS, EffectSpec
from custom_components.twinkly_canvas.store import Active

from .fake_device import FakeTwinkly

ENTITY = "light.test_tree"
RED = (255, 0, 0, 0)
SILVER = (192, 192, 192, 0)
GOLD = (255, 215, 0, 0)
OFF = (0, 0, 0, 0)


async def _call(hass: HomeAssistant, action: str, **data) -> None:
    await hass.services.async_call(
        DOMAIN, action, {"entity_id": ENTITY, **data}, blocking=True
    )


async def _refused(hass: HomeAssistant, action: str, **data) -> ServiceValidationError:
    """The validation error the action raises."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, action, **data)
    return err.value


def _painted(canvas: Canvas, color: tuple) -> list[int]:
    return [i for i, p in enumerate(canvas.pixels) if p == color]


async def test_set_leds_service(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    store = entry.runtime_data.store
    store.set_canvas(Canvas(400, [RED] * 400))
    await _call(hass, "set_leds", leds="38-80 odd", color_name="silver")
    assert _painted(store.canvas, SILVER) == list(range(38, 79, 2))  # LEDs 39, 41 ... 79
    assert store.canvas.pixels[37] == RED  # LED 38 unchanged
    assert store.canvas.pixels.count(RED) == 400 - 21
    assert fake.playing.frames == 1
    assert fake.playing.data == store.canvas.to_frame("RGBW")
    assert store.active == Active("painted", None)


async def test_set_leds_clear_others(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    store = entry.runtime_data.store
    store.set_canvas(Canvas(400, [RED] * 400))
    await _call(
        hass, "set_leds", leds="1-10", color=[255, 0, 0], white=40, clear_others=True
    )
    assert store.canvas.pixels == [(255, 0, 0, 40)] * 10 + [OFF] * 390
    assert fake.playing.data == store.canvas.to_frame("RGBW")


async def test_set_leds_bad_selection(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    calls = len(fake.calls)
    err = await _refused(hass, "set_leds", leds="0-3", color_name="red")
    assert err.translation_key == "invalid_selection"
    assert err.translation_placeholders == {"error": "LED 0 is outside 1-400"}
    assert str(err) == "Invalid LED selection: LED 0 is outside 1-400"
    assert fake.calls[calls:] == []  # nothing reached the lights


async def test_set_leds_needs_one_color(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    calls = len(fake.calls)
    for colors in (
        {},
        {"color": [255, 0, 0], "color_name": "red"},
        {"color_name": "chartreuse"},
        {"color": [255, 0]},
    ):
        err = await _refused(hass, "set_leds", leds="1-3", **colors)
        assert err.translation_key == "invalid_color", colors
    assert str(err).startswith("Invalid colour. Use an RGB colour")
    assert fake.calls[calls:] == []


async def test_play_effect_service(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    store = entry.runtime_data.store
    await _call(
        hass,
        "play_effect",
        kind="chase",
        colors=["red", [192, 192, 192]],
        leds="1-20",
        speed=100,
    )
    assert store.custom_effect == EffectSpec(
        "chase", tuple(range(20)), (RED, SILVER), speed=1.0
    )
    assert store.custom_effect.kind == "chase"
    assert fake.playing.frames > 1
    assert store.active == Active("custom_effect", None)
    assert hass.states.get(ENTITY).attributes["effect"] == "Custom effect"

    await _call(hass, "play_effect", kind="twinkle")  # the defaults
    assert store.custom_effect == EffectSpec("twinkle", tuple(range(400)), speed=0.5)

    err = await _refused(hass, "play_effect", kind="chase", colors=[0xFF0000])
    assert err.translation_key == "invalid_color"
    err = await _refused(hass, "play_effect", kind="chase", leds="1-401")
    assert err.translation_key == "invalid_selection"


async def test_play_effect_refused_by_lights(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    fake.max_movies = len(fake.movies)  # the lights hold no more movies
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "play_effect", kind="chase")
    assert err.value.translation_key == "too_many_movies"
    assert entry.runtime_data.store.custom_effect is None  # only an effect that played


async def test_apply_unknown_look(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    err = await _refused(hass, "apply_look", look="Missing")
    assert err.translation_key == "unknown_look"
    assert err.translation_placeholders == {"name": "Missing"}


async def test_save_and_apply_look(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    store = entry.runtime_data.store
    await _call(hass, "set_leds", leds="1-10", color_name="gold")
    await _call(hass, "save_look", name="Gold")
    look = store.looks["Gold"]
    assert (_painted(look.canvas, GOLD), look.effect) == (list(range(10)), None)
    assert "Look: Gold" in hass.states.get(ENTITY).attributes["effect_list"]

    await _call(hass, "set_leds", leds="all", color_name="off")
    await _call(hass, "apply_look", look="Gold")
    assert fake.playing.data == look.canvas.to_frame("RGBW")
    assert store.active == Active("look", "Gold")

    err = await _refused(hass, "save_look", name=" ")
    assert err.translation_key == "invalid_name"
    assert str(err) == "Invalid name: A look name needs 1 to 40 characters"


async def test_save_look_from_effect_or_look(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    store = entry.runtime_data.store
    await _call(hass, "play_effect", kind="breathe", leds="1-50")
    await _call(hass, "save_look", name="Breathing")
    assert store.looks["Breathing"].effect == store.custom_effect  # a moving look

    await _call(hass, "apply_look", look="Breathing")
    await _call(hass, "save_look", name="Copy")
    copy, source = store.looks["Copy"], store.looks["Breathing"]
    assert (copy.canvas.pixels, copy.effect) == (source.canvas.pixels, source.effect)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ENTITY, "effect": "Built-in 2"}, blocking=True
    )
    err = await _refused(hass, "save_look", name="Built-in")
    assert err.translation_key == "cannot_save_look"
    assert str(err).startswith("A look can be saved from a painting")
    assert "Built-in" not in store.looks


async def test_find_leds_service(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ENTITY, "effect": "Built-in 3"}, blocking=True
    )
    task = hass.async_create_task(_call(hass, "find_leds", leds="1-2"))
    while not task.done():  # it sleeps between real-time frames
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=0.5))
        await asyncio.sleep(0)
    await task
    white, dark = bytes([0, 255, 255, 255]), bytes(4)
    assert fake.rt_frames[0][:12] == white * 2 + dark
    assert fake.rt_frames[1][:12] == dark * 3
    assert (fake.mode, fake.effect_id) == ("effect", 2)  # what played plays again

    err = await _refused(hass, "find_leds", leds="odd")
    assert err.translation_key == "invalid_selection"


async def test_group_services(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeTwinkly
) -> None:
    store = entry.runtime_data.store
    await _call(hass, "save_group", name="Corners", leds="1, 100, 301, 400")
    assert store.groups == {"Corners": "1, 100, 301, 400"}
    await _call(hass, "set_leds", leds="Corners", color_name="red")
    assert _painted(store.canvas, RED) == [0, 99, 300, 399]

    await _call(hass, "delete_group", name="Corners")
    assert store.groups == {}
    err = await _refused(hass, "set_leds", leds="Corners", color_name="red")
    assert err.translation_key == "invalid_selection"

    err = await _refused(hass, "save_group", name="odd", leds="1-3")
    assert err.translation_key == "invalid_name"
    err = await _refused(hass, "save_group", name="Top", leds="1-401")
    assert err.translation_key == "invalid_selection"
    assert store.groups == {}


async def test_action_descriptions(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """services.yaml, the translations and the icons cover every registered action."""
    path = (await async_get_integration(hass, DOMAIN)).file_path / "services.yaml"
    actions = await hass.async_add_executor_job(load_yaml_dict, str(path))
    names = await translation.async_get_translations(hass, "en", "services", [DOMAIN])
    labels = await translation.async_get_translations(hass, "en", "selector", [DOMAIN])
    icons = (await icon.async_get_icons(hass, "services", [DOMAIN]))[DOMAIN]
    assert set(actions) == set(icons) == set(hass.services.async_services_for_domain(DOMAIN))
    for action, description in actions.items():
        assert description["target"] == {"entity": {"integration": DOMAIN, "domain": "light"}}
        key = f"component.{DOMAIN}.services.{action}"
        assert names[f"{key}.name"]
        assert names[f"{key}.description"]
        for field, spec in description["fields"].items():
            assert names[f"{key}.fields.{field}.name"]
            selector.validate_selector(spec["selector"])

    selects = {
        "color_name": (actions["set_leds"]["fields"]["color_name"], NAMED_COLORS),
        "effect_kind": (actions["play_effect"]["fields"]["kind"], EFFECT_KINDS),
    }
    for translation_key, (field, values) in selects.items():
        select = field["selector"]["select"]
        assert select["translation_key"] == translation_key
        assert select["options"] == [v.replace(" ", "_") for v in values]  # "off" a string
        for option in select["options"]:
            assert labels[f"component.{DOMAIN}.selector.{translation_key}.options.{option}"]
