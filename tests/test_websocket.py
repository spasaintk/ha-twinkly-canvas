"""Tests for the card's websocket commands, run against the simulated device."""

import asyncio
import base64
from datetime import timedelta

import pytest
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.components.light.const import DATA_COMPONENT as LIGHT_ENTITIES
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant.typing import MockHAClientWebSocket, WebSocketGenerator

from custom_components.twinkly_canvas.canvas import Canvas
from custom_components.twinkly_canvas.colors import NAMED_COLORS
from custom_components.twinkly_canvas.const import DOMAIN
from custom_components.twinkly_canvas.effects import (
    EFFECT_KINDS,
    EffectSpec,
    loop_length,
    render,
)
from custom_components.twinkly_canvas.store import Active, Look

from .fake_device import APP_MOVIE_FRAMES, FakeTwinkly, advance

ENTITY = "light.test_tree"
RED = (255, 0, 0, 0)
GOLD = (255, 215, 0, 0)
SILVER = (192, 192, 192, 0)
WARM = (0, 0, 0, 255)
OFF = (0, 0, 0, 0)
CHASE = {
    "kind": "chase",
    "leds": list(range(20)),
    "colors": [list(RED), list(SILVER)],
    "speed": 100,
    "keep_others": False,
}


@pytest.fixture
async def ws(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> MockHAClientWebSocket:
    """A websocket client of an admin user; the entry is set up."""
    return await hass_ws_client(hass)


async def _send(ws: MockHAClientWebSocket, command: str, **data) -> dict:
    """The reply to one command; entity_id is the test light unless data names another."""
    await ws.send_json_auto_id(
        {"type": f"{DOMAIN}/{command}", "entity_id": ENTITY, **data}
    )
    return await ws.receive_json()


async def _ok(ws: MockHAClientWebSocket, command: str, **data):
    """The result of a command that succeeds."""
    reply = await _send(ws, command, **data)
    assert reply["success"], reply
    return reply["result"]


async def _error(ws: MockHAClientWebSocket, command: str, **data) -> dict:
    """The error ({"code", "message"}) of a command that fails."""
    reply = await _send(ws, command, **data)
    assert not reply["success"], reply
    return reply["error"]


def _painted(canvas: Canvas, color: tuple) -> list[int]:
    return [i for i, p in enumerate(canvas.pixels) if p == color]


def _attr(hass: HomeAssistant, name: str):
    return hass.states.get(ENTITY).attributes[name]


async def test_state(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    used = sum(APP_MOVIE_FRAMES) + 4 * len(APP_MOVIE_FRAMES)  # 4 overhead frames each
    assert await _ok(ws, "state") == {
        "led_count": 400,
        "strings": [[0, 200], [200, 200]],
        "profile": "RGBW",
        "canvas": Canvas(400).to_b64(),
        "groups": {},
        "looks": [],
        "custom_effect": None,
        "builtins": 5,
        "app_movies": [  # unique_id lower-cased, like active.ref of an app movie
            {
                "id": i,
                "name": f"App movie {i + 1}",
                "frames": frames,
                "unique_id": fake.movies[i].unique_id.lower(),
            }
            for i, frames in enumerate(APP_MOVIE_FRAMES)
        ],
        "memory": {
            "used_pct": pytest.approx(100 * used / 2536),
            "available_frames": 2536 - used,
            "max_capacity": 2536,
            "movies": 7,
            "max_movies": 55,
            "stranded": 0,
        },
        "active": {"type": "builtin", "ref": "0"},  # what the device plays
        "mode": "effect",
        "colors": {name: list(color) for name, color in NAMED_COLORS.items()},
        "effects": list(EFFECT_KINDS),
    }

    data = entry.runtime_data
    data.store.save_group("Top", "1-10")
    moving = Look("Moving", Canvas(400, [RED] * 400), EffectSpec("chase", (0, 1, 2)))
    data.store.save_look(moving)
    data.store.save_look(Look("angel", Canvas(400), None))
    data.store.set_custom_effect(EffectSpec("twinkle", (5,)))
    await data.device.async_show_canvas(
        Canvas(400), Active("painted", None)
    )  # ours: id 7
    later = fake.add_app_movie("Later", 10)  # strands our movie
    await data.coordinator.async_refresh()

    state = await _ok(ws, "state")
    assert state["groups"] == {"Top": "1-10"}
    assert state["looks"] == [  # sorted like the effect list, ignoring case
        {
            "name": "angel",
            "kind": "static",
            "canvas": Canvas(400).to_b64(),
            "effect": None,
        },
        {
            "name": "Moving",
            "kind": "moving",
            "canvas": moving.canvas.to_b64(),
            "effect": moving.effect.to_dict(),
        },
    ]
    assert state["custom_effect"] == EffectSpec("twinkle", (5,)).to_dict()
    assert [m["id"] for m in state["app_movies"]] == [0, 1, 2, 3, 4, 5, 6, 8]
    assert state["app_movies"][-1] == {
        "id": 8,
        "name": "Later",
        "frames": 10,
        "unique_id": later.unique_id.lower(),
    }
    assert (state["memory"]["movies"], state["memory"]["stranded"]) == (9, 1)
    assert state["mode"] == "movie"


@pytest.mark.parametrize("capacity", [0, -1])
async def test_state_memory_without_capacity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
    capacity: int,
) -> None:
    fake.max_capacity = capacity
    await entry.runtime_data.coordinator.async_refresh()
    assert (await _ok(ws, "state"))["memory"]["used_pct"] == 0


async def test_paint_live_previews(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    assert await _ok(ws, "paint", leds=[0, 2, 4], color=list(RED)) is None
    assert _painted(store.canvas, RED) == [0, 2, 4]
    assert fake.rt_frames == []  # debounced
    await advance(hass, 0.6)
    assert fake.mode == "rt"
    assert fake.rt_frames == [store.canvas.to_frame("RGBW")]
    await advance(hass, 3)  # painting paused: stored on the lights
    assert fake.playing.data == store.canvas.to_frame("RGBW")
    assert store.active == Active("painted", None)


async def test_paint_not_live_then_commit(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    await _ok(ws, "paint", leds=list(range(10)), color=list(WARM), live=False)
    assert store.canvas.pixels[:11] == [WARM] * 10 + [OFF]
    await advance(hass, 10)
    assert (fake.rt_frames, fake.mode) == ([], "effect")  # nothing reached the lights

    assert await _ok(ws, "commit") is None
    assert fake.playing.data == store.canvas.to_frame("RGBW")
    assert store.active == Active("painted", None)
    assert _attr(hass, "effect") == "Painted"


async def _light(hass: HomeAssistant, service: str) -> None:
    """The light's own turn_on or turn_off, as a dashboard toggle calls it."""
    await hass.services.async_call(
        "light", service, {"entity_id": ENTITY}, blocking=True
    )


async def test_live_then_not_live_then_commit(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    await _ok(ws, "paint", leds=[0], color=list(RED))
    await advance(hass, 0.6)
    await _ok(ws, "paint", leds=[1], color=list(WARM), live=False)
    await _ok(ws, "commit")
    assert store.canvas.pixels[:3] == [RED, WARM, OFF]
    assert fake.playing.data == store.canvas.to_frame("RGBW")


async def test_live_then_not_live_then_timer(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    await _ok(ws, "paint", leds=[0], color=list(RED))
    await advance(hass, 0.6)
    await _ok(ws, "paint", leds=[1], color=list(WARM), live=False)
    await advance(hass, 3)  # painting paused: stored on the lights
    assert store.canvas.pixels[:3] == [RED, WARM, OFF]
    assert fake.playing.data == store.canvas.to_frame("RGBW")
    uploads = fake.calls.count("new_movie")
    await _ok(ws, "commit")
    assert fake.calls.count("new_movie") == uploads  # the lights already show it


async def test_turn_off_during_live_keeps_painting(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    await _ok(ws, "builtin", effect_id=2)  # what played before the painting
    await _ok(ws, "paint", leds=[0], color=list(RED))
    await _light(hass, "turn_off")  # at once: before even the live frame
    await _light(hass, "turn_on")
    assert store.canvas.pixels[0] == RED
    assert fake.playing.data == store.canvas.to_frame("RGBW")
    assert store.active == Active("painted", None)


@pytest.mark.parametrize(
    ("command", "data"), [("commit", {}), ("effect/play", {"spec": CHASE})]
)
async def test_failed_upload_forgets_movie(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
    command: str,
    data: dict,
) -> None:
    store = entry.runtime_data.store
    await _ok(ws, "paint", leds=[0], color=list(RED), live=False)
    await _ok(ws, "commit")
    older = fake.playing
    fake.add_app_movie("Later", 10)  # only the last movie can go: the older one stays
    fake.fail_upload = True
    await _ok(ws, "paint", leds=[1], color=list(WARM), live=False)
    assert (await _error(ws, command, **data))["code"] == "device_error"
    assert store.current_movie_uid is None

    fake.fail_upload = False
    await _light(hass, "turn_off")
    await _light(hass, "turn_on")
    assert fake.playing is not older
    assert fake.playing.data == store.canvas.to_frame("RGBW")


async def test_app_movie_refuses_ours(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    await _ok(ws, "paint", leds=[0], color=list(RED), live=False)
    await _ok(ws, "commit")  # our movie: id 7
    ours = fake.playing
    await _ok(ws, "paint", leds=[1], color=list(WARM))  # live: a commit is pending
    calls = len(fake.calls)
    error = await _error(ws, "app_movie", movie_id=7)
    assert error == {
        "code": "invalid_format",
        "message": 'These lights have no effect "7"',
    }
    assert fake.calls[calls:] == ["get_movies"]  # looked up, nothing changed
    assert (fake.playing, store.active) == (ours, Active("painted", None))
    await advance(hass, 3)  # the pending painting was kept
    assert fake.playing.data == store.canvas.to_frame("RGBW")


async def test_find(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    await _ok(ws, "builtin", effect_id=2)
    reply = hass.async_create_task(_send(ws, "find", leds=[0, 1]))
    while not reply.done():  # it sleeps between real-time frames
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=0.5))
        await asyncio.sleep(0)
    assert (await reply)["success"]
    white, dark = bytes([0, 255, 255, 255]), bytes(4)
    assert fake.rt_frames[0][:12] == white * 2 + dark
    assert (fake.mode, fake.effect_id) == ("effect", 2)  # what played plays again


def _card_bytes(frame: bytes) -> bytes:
    """Device bytes (W,R,G,B per LED) in the card's order R,G,B,W."""
    return bytes(
        v for i in range(0, len(frame), 4) for v in (*frame[i + 1 : i + 4], frame[i])
    )


async def test_effect_render(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    store.set_canvas(Canvas(400, [(10, 20, 30, 40)] * 400))
    calls = len(fake.calls)

    spec = {
        "kind": "twinkle",
        "leds": "all",
        "colors": [],
        "speed": 50,
        "keep_others": True,
    }
    result = await _ok(ws, "effect/render", spec=spec)
    effect = EffectSpec("twinkle", tuple(range(400)))
    assert loop_length(effect, 11) == 132  # longer than the preview
    assert result["fps"] == 11
    assert len(result["frames"]) == min(120, loop_length(effect, 11))
    frames = [base64.b64decode(f) for f in result["frames"]]
    assert all(len(f) == 1600 for f in frames)
    # A seamless 120-frame loop, not 120 frames cut from the 132-frame loop
    assert frames == [
        _card_bytes(f) for f in render(effect, store.canvas, 11, "RGBW", 120)
    ]

    spec = {**CHASE, "colors": [list(RED)], "keep_others": True}
    result = await _ok(ws, "effect/render", spec=spec)
    effect = EffectSpec("chase", tuple(range(20)), (RED,), speed=1.0)
    assert len(result["frames"]) == min(120, loop_length(effect, 11))
    for frame in map(base64.b64decode, result["frames"]):
        assert frame[-4:] == bytes(
            [10, 20, 30, 40]
        )  # LED 400 keeps its colour: R,G,B,W
    assert fake.calls[calls:] == []  # a preview stays on the card


async def test_effect_play(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    assert await _ok(ws, "effect/play", spec=CHASE) is None
    effect = EffectSpec(
        "chase", tuple(range(20)), (RED, SILVER), 1.0, keep_others=False
    )
    assert store.custom_effect == effect
    assert store.active == Active("custom_effect", None)
    assert fake.playing.data == b"".join(render(effect, store.canvas, 11, "RGBW"))
    assert _attr(hass, "effect") == "Custom effect"

    breathe = {
        "kind": "breathe",
        "leds": "1-20 odd",  # selection text instead of indices
        "colors": [],
        "speed": 0,
        "keep_others": True,
    }
    await _ok(ws, "effect/play", spec=breathe)
    assert store.custom_effect == EffectSpec(
        "breathe", tuple(range(0, 20, 2)), speed=0.0
    )


async def test_builtin_and_app_movie(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    assert await _ok(ws, "builtin", effect_id=2) is None  # the device's id, from 0
    assert (fake.mode, fake.effect_id) == ("effect", 2)
    assert store.active == Active("builtin", "2")
    assert _attr(hass, "effect") == "Built-in 3"

    assert await _ok(ws, "app_movie", movie_id=3) is None
    assert fake.playing is fake.movies[3]
    assert store.active == Active("app_movie", fake.movies[3].unique_id.lower())
    await advance(hass, 10)  # the refresh after a refresh waits for its cooldown
    assert _attr(hass, "effect") == "App: App movie 4"
    state = await _ok(ws, "state")  # the card finds the playing movie by its unique_id
    assert state["active"]["ref"] == state["app_movies"][3]["unique_id"]

    assert (await _error(ws, "builtin", effect_id=5))["code"] == "invalid_format"
    assert (await _error(ws, "app_movie", movie_id=7))["code"] == "invalid_format"


async def test_look_lifecycle(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    await _ok(ws, "paint", leds=list(range(10)), color=list(GOLD), live=False)
    await _ok(ws, "commit")
    assert await _ok(ws, "look/save", name="Gold") is None
    assert (_painted(store.looks["Gold"].canvas, GOLD), store.looks["Gold"].effect) == (
        list(range(10)),
        None,
    )

    assert await _ok(ws, "look/rename", name="Gold", new_name="Golden") is None
    assert list(store.looks) == ["Golden"]
    assert "Look: Golden" in _attr(hass, "effect_list")
    assert "Look: Gold" not in _attr(hass, "effect_list")

    await _ok(ws, "paint", leds=[10], color=list(RED), live=False)
    await _ok(ws, "commit")
    assert await _ok(ws, "look/update", name="Golden") is None
    assert store.looks["Golden"].canvas.pixels[9:12] == [GOLD, RED, OFF]

    await _ok(ws, "paint", leds=list(range(400)), color=list(OFF), live=False)
    await _ok(ws, "commit")
    await _ok(ws, "look/save", name="Dark")
    assert await _ok(ws, "look/apply", name="Golden") is None
    assert fake.playing.data == store.looks["Golden"].canvas.to_frame("RGBW")
    assert store.active == Active("look", "Golden")

    error = await _error(ws, "look/rename", name="Golden", new_name="Dark")
    assert error == {
        "code": "invalid_format",
        "message": "A look named 'Dark' already exists",
    }
    assert await _ok(ws, "look/delete", name="Golden") is None
    assert list(store.looks) == ["Dark"]
    assert store.active == Active("painted", None)  # the deleted look was active
    assert "Look: Golden" not in _attr(hass, "effect_list")

    for command, data in (
        ("look/apply", {"name": "Golden"}),
        ("look/update", {"name": "Golden"}),
        ("look/rename", {"name": "Golden", "new_name": "Gilded"}),
    ):
        assert await _error(ws, command, **data) == {
            "code": "invalid_format",
            "message": 'There is no look named "Golden"',  # HA drops the final full stop
        }
    assert list(store.looks) == ["Dark"]


async def test_active_reads_the_device_first(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    """What plays comes from the device; only HA's own content can become a look."""
    coordinator = entry.runtime_data.coordinator
    fake.effect_id = 2  # the app picks another effect behind HA's back
    await coordinator.async_request_refresh()  # a poll; the next one waits 10 s
    assert (await _ok(ws, "state"))["active"] == {"type": "builtin", "ref": "2"}
    error = await _error(ws, "look/save", name="Now")
    assert error["message"].startswith("A look can be saved from a painting")

    await _ok(ws, "paint", leds=[0], color=list(RED), live=False)
    await _ok(ws, "commit")
    assert coordinator.data.mode == "effect"  # the last poll is older than the commit
    assert (await _ok(ws, "state"))["active"] == {"type": "painted", "ref": None}
    await _ok(ws, "look/save", name="Now")
    assert entry.runtime_data.store.looks["Now"].canvas.pixels[:2] == [RED, OFF]


async def test_look_commands_ignore_case(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    await _ok(ws, "commit")  # the painting plays: it can become a look
    await _ok(ws, "look/save", name="Gold")
    await _ok(ws, "look/update", name=" GOLD ")  # replaces it under the new spelling
    assert list(store.looks) == ["GOLD"]
    await _ok(ws, "look/rename", name="gold", new_name="Golden")
    assert list(store.looks) == ["Golden"]
    await _ok(ws, "look/delete", name=" golden ")
    assert store.looks == {}


async def test_group_save_and_delete(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    assert await _ok(ws, "group/save", name="Corners", leds="1, 100, 301, 400") is None
    assert store.groups == {"Corners": "1, 100, 301, 400"}
    assert (await _ok(ws, "state"))["groups"] == {"Corners": "1, 100, 301, 400"}

    assert await _ok(ws, "group/delete", name="Corners") is None
    assert store.groups == {}
    assert await _error(ws, "group/delete", name="Corners") == {
        "code": "invalid_format",
        "message": 'There is no group named "Corners"',
    }

    for name, leds in (("odd", "1-3"), ("Top", "1-401")):
        error = await _error(ws, "group/save", name=name, leds=leds)
        assert error["code"] == "invalid_format"
    assert error["message"] == "Invalid LED selection: LED 401 is outside 1-400"
    assert store.groups == {}


class _OtherLight(LightEntity):
    """A light that is not one of ours."""

    _attr_name = "Other"
    _attr_is_on = True
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}


async def test_unknown_entity_errors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    ws: MockHAClientWebSocket,
) -> None:
    await hass.data[LIGHT_ENTITIES].async_add_entities([_OtherLight()])
    assert hass.states.get("light.other") is not None
    for entity_id in ("light.missing", "light.other"):
        error = await _error(ws, "state", entity_id=entity_id)
        assert error == {
            "code": "not_found",
            "message": f"{entity_id} is not a Twinkly Canvas light",
        }

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert (await _error(ws, "commit"))["code"] == "not_found"


async def test_bad_leds_errors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    store = entry.runtime_data.store
    calls = len(fake.calls)
    for command, data in (
        ("paint", {"leds": [399, 400], "color": list(RED)}),
        ("paint", {"leds": [-1], "color": list(RED)}),
        ("paint", {"leds": "1-3", "color": list(RED)}),  # a list of indices
        ("paint", {"leds": [0], "color": [256, 0, 0, 0]}),
        ("paint", {"leds": [0], "color": [255, 0, 0]}),  # needs r, g, b and w
        ("find", {"leds": [400]}),
        ("effect/render", {"spec": {**CHASE, "leds": [400]}}),
        ("effect/play", {"spec": {**CHASE, "leds": [400]}}),
        ("effect/play", {"spec": {**CHASE, "leds": "1-401"}}),
        ("effect/play", {"spec": {**CHASE, "colors": [[255, 0]]}}),
        ("effect/play", {"spec": {**CHASE, "speed": 101}}),
    ):
        error = await _error(ws, command, **data)
        assert error["code"] == "invalid_format", (command, data)
    assert store.canvas.pixels == [OFF] * 400
    assert store.custom_effect is None
    await advance(hass, 10)
    assert fake.calls[calls:] == []  # nothing reached the lights


async def test_paint_names_leds_from_1(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    ws: MockHAClientWebSocket,
) -> None:
    """The card's 0-based indices are named like LED numbers in a selection."""
    for leds, message in (
        ([0, 400], "LED 401 is outside 1-400"),
        ([-1], "LED 0 is outside 1-400"),
    ):
        error = await _error(ws, "paint", leds=leds, color=list(RED))
        assert error == {"code": "invalid_format", "message": message}
    assert entry.runtime_data.store.canvas.pixels == [OFF] * 400


async def test_device_errors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake: FakeTwinkly,
    ws: MockHAClientWebSocket,
) -> None:
    fake.max_movies = len(fake.movies)  # the lights hold no more movies
    error = await _error(ws, "effect/play", spec=CHASE)
    assert error["code"] == "device_error"
    assert error["message"].startswith(
        "The lights hold as many movies as they can store"
    )

    fake.fail_with_connection_error = True
    error = await _error(ws, "builtin", effect_id=1)
    assert error["code"] == "device_error"
    assert error["message"].startswith(
        "The Twinkly lights could not carry out the command"
    )


async def test_read_only_user_refused(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    assert (await _ok(ws, "state"))[
        "led_count"
    ] == 400  # reading needs read permission only
    error = await _error(ws, "paint", leds=[0], color=list(RED))
    assert error["code"] == "unauthorized"
    assert entry.runtime_data.store.canvas.pixels == [OFF] * 400
