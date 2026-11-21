"""Tests for the persistent store."""

import logging
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import async_fire_time_changed_exact

from custom_components.twinkly_canvas.canvas import OFF, Canvas
from custom_components.twinkly_canvas.effects import EffectSpec
from custom_components.twinkly_canvas.selection import SelectionError
from custom_components.twinkly_canvas.store import Active, CanvasStore, Look

ENTRY_ID = "test-entry"
KEY = f"twinkly_canvas.{ENTRY_ID}"
LEDS = 4
RED = (255, 0, 0, 0)
WARM = (0, 0, 0, 255)


async def _load(hass: HomeAssistant) -> CanvasStore:
    store = CanvasStore(hass, ENTRY_ID, LEDS)
    await store.async_load()
    return store


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Move the clock on and let any due delayed save finish."""
    freezer.tick(seconds)
    async_fire_time_changed_exact(hass)
    await hass.async_block_till_done()


def _values(store: CanvasStore) -> tuple:
    """Everything the store holds, in comparable form."""
    return (
        store.canvas.pixels,
        store.groups,
        {
            name: (look.name, look.canvas.pixels, look.effect, look.kind)
            for name, look in store.looks.items()
        },
        store.custom_effect,
        store.our_movies,
        store.current_movie_uid,
        store.active,
    )


async def test_defaults(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    store = await _load(hass)
    assert store.canvas.pixels == [OFF] * LEDS
    assert store.groups == {}
    assert store.looks == {}
    assert store.custom_effect is None
    assert store.our_movies == []
    assert store.current_movie_uid is None
    assert store.active == Active("painted", None)
    assert KEY not in hass_storage
    with pytest.raises(ValueError):
        Active("bogus", None)


async def test_persist_roundtrip(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    store = await _load(hass)
    chase = EffectSpec("chase", (0, 2), (RED,), speed=0.25, keep_others=False, seed=7)
    painted = Canvas(LEDS, [RED, OFF, WARM, RED])
    static = Canvas(LEDS, [WARM] * LEDS)
    with patch.object(
        store._store, "async_delay_save", wraps=store._store.async_delay_save
    ) as delay_save:
        store.set_canvas(painted)
        store.save_look(Look("Draft", static, None))
        painted.fill(OFF)  # the store keeps its own copies
        static.fill(OFF)
        store.rename_look("Draft", "Static")
        store.save_look(Look("Moving", Canvas(LEDS, [RED] * LEDS), chase))
        store.save_look(Look("Spare", Canvas(LEDS), None))
        store.delete_look("Spare")
        store.save_group("  Corners ", "1, 4")
        store.save_group("Spare", "2")
        store.delete_group("Spare")
        store.set_custom_effect(EffectSpec("twinkle", (0, 1, 2, 3)))
        store.add_our_movie("0A1B-UID")
        store.add_our_movie("0a1b-uid")  # already ours
        store.add_our_movie("2c3d-uid")
        store.remove_our_movie("2C3D-UID")
        store.current_movie_uid = "0A1B-UID"
        store.active = Active("look", "Moving")
        assert store.next_movie_name() == "Canvas 1"
    assert delay_save.call_count == 17  # one per mutation above
    assert {call.args[1] for call in delay_save.call_args_list} == {1.0}

    await _advance(hass, freezer, 0.9)
    assert KEY not in hass_storage  # still inside the 1 s save delay
    await _advance(hass, freezer, 0.2)
    stored = hass_storage[KEY]
    assert stored["version"] == 1
    assert set(stored["data"]) == {
        "canvas",
        "groups",
        "looks",
        "custom_effect",
        "our_movies",
        "current_movie_uid",
        "active",
        "movie_counter",
    }
    assert stored["data"]["looks"]["Static"] == {
        "canvas": Canvas(LEDS, [WARM] * LEDS).to_b64(),
        "effect": None,
    }

    fresh = await _load(hass)
    assert _values(fresh) == _values(store)
    assert fresh.canvas.pixels == [RED, OFF, WARM, RED]
    assert fresh.groups == {"Corners": "1, 4"}
    assert fresh.our_movies == ["0a1b-uid"]
    assert fresh.current_movie_uid == "0a1b-uid"
    assert fresh.looks["Static"].kind == "static"
    assert fresh.looks["Moving"].kind == "moving"
    assert fresh.next_movie_name() == "Canvas 2"

    await fresh.async_remove()
    assert KEY not in hass_storage


async def test_rename_look_conflict(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _load(hass)
    store.save_look(Look("A", Canvas(LEDS, [RED] * LEDS), None))
    store.save_look(Look("B", Canvas(LEDS), None))
    store.active = Active("look", "A")

    with pytest.raises(ValueError):
        store.rename_look("A", "B")
    assert store.looks["A"].canvas.pixels == [RED] * LEDS
    assert store.looks["B"].canvas.pixels == [OFF] * LEDS

    store.rename_look("A", "C")
    assert sorted(store.looks) == ["B", "C"]
    assert store.looks["C"].name == "C"
    assert store.looks["C"].canvas.pixels == [RED] * LEDS
    assert store.active == Active("look", "C")

    store.delete_look("B")
    assert list(store.looks) == ["C"]


async def test_group_validation(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _load(hass)
    for name, spec in (("", "1"), ("   ", "1"), ("x" * 41, "1"), ("Corners", "0-3")):
        with pytest.raises(ValueError):
            store.save_group(name, spec)
    assert store.groups == {}

    store.save_group("x" * 40, "1-3")
    assert store.groups == {"x" * 40: "1-3"}
    store.delete_group("x" * 40)
    assert store.groups == {}


async def test_group_names_unique_ignoring_case(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _load(hass)
    with pytest.raises(SelectionError):
        store.save_group("5", "1-3")  # would redefine LED 5
    store.save_group("Corners", "1, 4")
    store.save_group("corners", "2, 3")
    assert store.groups == {"corners": "2, 3"}
    store.delete_group(" CORNERS ")
    assert store.groups == {}


async def test_look_names(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    store = await _load(hass)
    for bad in ("", "   ", "x" * 41):
        with pytest.raises(ValueError):
            store.save_look(Look(bad, Canvas(LEDS), None))
    store.save_look(Look("  Xmas ", Canvas(LEDS, [RED] * LEDS), None))
    store.save_look(Look("x" * 40, Canvas(LEDS), None))
    assert sorted(store.looks) == ["Xmas", "x" * 40]
    assert store.looks["Xmas"].name == "Xmas"
    for bad in ("", "x" * 41):
        with pytest.raises(ValueError):
            store.rename_look("Xmas", bad)
    store.active = Active("look", "Xmas")

    with patch.object(store._store, "async_delay_save") as delay_save:
        store.rename_look("Xmas", "Xmas")
        store.rename_look("Xmas", " Xmas ")
    delay_save.assert_not_called()
    assert sorted(store.looks) == ["Xmas", "x" * 40]

    store.rename_look("Xmas", "XMAS")  # only the case changes
    assert sorted(store.looks) == ["XMAS", "x" * 40]
    assert store.looks["XMAS"].canvas.pixels == [RED] * LEDS
    assert store.active == Active("look", "XMAS")
    with pytest.raises(KeyError):
        store.rename_look("Missing", "Other")


async def test_look_names_unique_ignoring_case(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _load(hass)
    store.save_look(Look("Xmas", Canvas(LEDS, [RED] * LEDS), None))
    store.active = Active("look", "Xmas")
    store.save_look(Look("XMAS", Canvas(LEDS), None))  # replaces it under the new spelling
    assert list(store.looks) == ["XMAS"]
    assert store.looks["XMAS"].canvas.pixels == [OFF] * LEDS
    assert store.active == Active("look", "XMAS")
    assert store.find_look(" xmas ") is store.looks["XMAS"]
    assert store.find_look("Missing") is None

    store.save_look(Look("A", Canvas(LEDS), None))
    store.save_look(Look("B", Canvas(LEDS), None))
    with pytest.raises(ValueError):
        store.rename_look("A", "b")
    store.rename_look("A", "a")  # only the case of the same look changes
    store.rename_look(" B ", "C")  # the old name matches ignoring case and whitespace
    assert sorted(store.looks) == ["C", "XMAS", "a"]

    store.delete_look(" xmas ")
    assert sorted(store.looks) == ["C", "a"]
    assert store.active == Active("painted", None)


async def test_delete_unknown_group(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _load(hass)
    store.save_group("Corners", "1, 4")
    with (
        patch.object(store._store, "async_delay_save") as delay_save,
        pytest.raises(KeyError),
    ):
        store.delete_group("Edges")
    delay_save.assert_not_called()
    assert store.groups == {"Corners": "1, 4"}


async def test_delete_active_look(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _load(hass)
    store.save_look(Look("A", Canvas(LEDS), None))
    store.save_look(Look("B", Canvas(LEDS), None))
    store.active = Active("look", "A")
    store.delete_look("B")
    assert store.active == Active("look", "A")
    store.delete_look("A")
    assert store.active == Active("painted", None)


async def test_state_is_changed_only_through_the_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    store = await _load(hass)
    with pytest.raises(AttributeError):
        store.active.ref = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        store.custom_effect = None  # type: ignore[misc]
    with pytest.raises(AttributeError):
        Look("A", Canvas(LEDS), None).name = "B"  # type: ignore[misc]


async def test_load_fits_canvases_to_led_count(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A device whose LED count changed keeps its canvas and looks: cut or padded with off."""
    stored = [(i % 256, 1, 2, 3) for i in range(300)]
    hass_storage[KEY] = {
        "version": 1,
        "data": {
            "canvas": Canvas(300, stored).to_b64(),
            "groups": {},
            "looks": {"Big": {"canvas": Canvas(500, [RED] * 500).to_b64(), "effect": None}},
            "custom_effect": None,
            "our_movies": [],
            "current_movie_uid": None,
            "active": {"type": "painted", "ref": None},
            "movie_counter": 0,
        },
    }
    store = CanvasStore(hass, ENTRY_ID, 400)
    await store.async_load()
    assert store.canvas.pixels == stored + [OFF] * 100
    assert store.looks["Big"].canvas.pixels == [RED] * 400
    warnings = [
        r
        for r in caplog.records
        if r.name == "custom_components.twinkly_canvas.store" and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1


@pytest.mark.parametrize(("leds", "kept"), [(range(400), range(300)), (range(300, 400), None)])
async def test_load_fits_effects_to_led_count(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    leds: range,
    kept: range | None,
) -> None:
    """Stored effects lose the LEDs a device no longer has; one left without LEDs goes."""
    effect = EffectSpec("chase", tuple(leds)).to_dict()
    hass_storage[KEY] = {
        "version": 1,
        "data": {
            "canvas": Canvas(300).to_b64(),
            "groups": {},
            "looks": {"Top": {"canvas": Canvas(300).to_b64(), "effect": effect}},
            "custom_effect": effect,
            "our_movies": [],
            "current_movie_uid": None,
            "active": {"type": "custom_effect", "ref": None},
            "movie_counter": 0,
        },
    }
    store = CanvasStore(hass, ENTRY_ID, 300)
    await store.async_load()
    fitted = EffectSpec("chase", tuple(kept)) if kept else None
    assert (store.custom_effect, store.looks["Top"].effect) == (fitted, fitted)
    warnings = [
        r
        for r in caplog.records
        if r.name == "custom_components.twinkly_canvas.store" and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1


async def test_next_movie_name_increments(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    store = await _load(hass)
    assert store.next_movie_name() == "Canvas 1"
    assert store.next_movie_name() == "Canvas 2"
    await _advance(hass, freezer, 1.1)
    assert (await _load(hass)).next_movie_name() == "Canvas 3"
