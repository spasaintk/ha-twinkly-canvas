"""Tests for the persistent store."""

from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import async_fire_time_changed_exact

from custom_components.twinkly_canvas.canvas import OFF, Canvas
from custom_components.twinkly_canvas.effects import EffectSpec
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
