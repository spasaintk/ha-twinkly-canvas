"""Tests for the device layer, run against the simulated device."""

import asyncio
from collections.abc import AsyncGenerator, Coroutine
from datetime import timedelta
import inspect
import math
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import async_fire_time_changed

from custom_components.twinkly_canvas.api import TwinklyClient, TwinklyConnectionError
from custom_components.twinkly_canvas.canvas import Canvas
from custom_components.twinkly_canvas.device import DeviceInfoData, TwinklyDevice
from custom_components.twinkly_canvas.effects import EffectSpec, loop_length, render
from custom_components.twinkly_canvas.movies import MovieState, stranded
from custom_components.twinkly_canvas.store import Active, CanvasStore, Look

from .fake_device import GESTALT, FakeMovie, FakeTwinkly

LEDS = 400
RED = (255, 0, 0, 0)
WARM = (0, 0, 0, 255)
CHASE = EffectSpec("chase", tuple(range(20)), speed=1.0)  # 9 frames at 11 fps


@pytest.fixture
def fake() -> FakeTwinkly:
    return FakeTwinkly()


@pytest.fixture
async def device(
    hass: HomeAssistant, fake: FakeTwinkly
) -> AsyncGenerator[TwinklyDevice]:
    info = DeviceInfoData.from_api(
        await fake.gestalt(), await fake.firmware_version(), await fake.led_config()
    )
    store = CanvasStore(hass, "test-entry", info.led_count)
    await store.async_load()
    device = TwinklyDevice(hass, fake, store, info)
    yield device
    await device.async_shutdown()


def _canvas(color: tuple = RED, leds: range = range(10)) -> Canvas:
    canvas = Canvas(LEDS)
    canvas.set(leds, color)
    return canvas


def _own(fake: FakeTwinkly, device: TwinklyDevice) -> list[FakeMovie]:
    """Movies on the fake that the integration recorded as its own."""
    return [m for m in fake.movies if m.unique_id.lower() in device.store.our_movies]


async def _advance(hass: HomeAssistant, seconds: float) -> None:
    """Run the timers due within the next seconds; not cumulative, the clock stays."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def _while_time_passes(hass: HomeAssistant, coro: Coroutine[Any, Any, None]) -> None:
    """Await coro while the clock jumps on 0.5 s at a time (it sleeps between frames)."""
    task = hass.async_create_task(coro)
    while not task.done():
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=0.5))
        await asyncio.sleep(0)
    await task


async def _lose_our_movie(device: TwinklyDevice, fake: FakeTwinkly) -> None:
    """Turn off, then delete our movie (the last one) behind the integration's back."""
    await device.async_turn_off()
    await fake.delete_movie(fake.movies[-1].unique_id)


def test_fake_mirrors_client() -> None:
    def methods(cls: type) -> dict[str, inspect.Signature]:
        return {
            name: inspect.signature(func)
            for name, func in vars(cls).items()
            if not name.startswith("_") and inspect.iscoroutinefunction(func)
        }

    assert methods(FakeTwinkly) == methods(TwinklyClient)
    assert FakeTwinkly().token_epoch == 0


def test_device_info_from_api() -> None:
    strings = {
        "strings": [{"first_led_id": 0, "length": 200}, {"first_led_id": 200, "length": 200}]
    }
    info = DeviceInfoData.from_api({**GESTALT, "mac": "AA-BB-CC-00-11-22"}, "2.8.18", strings)
    assert info == DeviceInfoData(
        mac="aa:bb:cc:00:11:22",
        name="Test Tree",
        model="TEST400",
        firmware="2.8.18",
        hardware="100",
        led_count=400,
        profile="RGBW",
        bytes_per_led=4,
        frame_rate=11.61,
        max_movies=55,
        strings=((0, 200), (200, 200)),
    )
    old = {k: v for k, v in GESTALT.items() if k not in ("led_profile", "max_movies")}
    info = DeviceInfoData.from_api({**old, "bytes_per_led": 3}, "2.6.0", None)
    assert (info.profile, info.max_movies, info.strings) == ("RGB", 15, ((0, 400),))


async def test_get_state(device: TwinklyDevice, fake: FakeTwinkly) -> None:
    state = await device.async_get_state()
    assert (state.mode, state.effect_id, state.effects_count) == ("effect", 0, 5)
    assert (state.brightness, state.current_movie) == (100, None)
    assert len(state.movies.movies) == 7
    assert (state.movies.available_frames, state.movies.max_movies) == (2124, 55)

    await device.async_set_brightness(40)
    await device.async_show_canvas(_canvas(), Active("painted", None))
    state = await device.async_get_state()
    assert (state.mode, state.effect_id, state.brightness) == ("movie", None, 40)
    assert state.current_movie == state.movies.movies[-1]
    assert state.current_movie.unique_id == device.store.current_movie_uid


async def test_show_canvas_uploads_and_plays(
    device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    canvas = _canvas()
    await device.async_show_canvas(canvas, Active("painted", None))
    (movie,) = fake.movies[7:]
    assert movie.frames == 1
    assert movie.data == canvas.to_frame("RGBW")
    assert fake.mode == "movie"
    assert fake.current is movie
    assert device.store.our_movies == [movie.unique_id.lower()]
    assert device.store.current_movie_uid == movie.unique_id.lower()
    assert device.store.canvas.pixels == canvas.pixels
    assert device.store.active == Active("painted", None)
    assert fake.rt_frames == [canvas.to_frame("RGBW")]  # the bridge while uploading


async def test_show_canvas_replaces_own_last_movie(
    device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    await device.async_show_canvas(_canvas(), Active("painted", None))
    await device.async_show_canvas(_canvas(WARM), Active("painted", None))
    (movie,) = _own(fake, device)
    assert movie is fake.movies[-1]
    assert len(fake.movies) == 8
    assert movie.data == _canvas(WARM).to_frame("RGBW")
    assert device.store.our_movies == [movie.unique_id.lower()]


async def test_never_touches_app_movies(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    device.store.save_look(Look("Static", _canvas(WARM), None))
    device.store.save_look(Look("Moving", _canvas(RED), CHASE))

    async def preview_then_commit() -> None:
        await device.async_request_preview(_canvas(WARM, range(5)))
        await device.async_commit_now()

    operations = [
        lambda: device.async_show_canvas(_canvas(), Active("painted", None)),
        lambda: device.async_play_effect(
            CHASE, device.store.canvas, Active("custom_effect", None)
        ),
        lambda: device.async_select_builtin(1),
        lambda: device.async_select_app_movie(3),
        lambda: device.async_apply_look("Static"),
        lambda: device.async_apply_look("Moving"),
        device.async_turn_off,
        device.async_turn_on,
        preview_then_commit,
        device.async_restore,
    ]
    before = [(m.unique_id, m.frames, m.data) for m in fake.movies]
    for operation in operations:
        await operation()
    late = fake.add_app_movie("Late movie", 12)  # the app stores a movie after ours
    late_id = len(fake.movies) - 1
    for operation in operations:
        await operation()
    assert [(m.unique_id, m.frames, m.data) for m in fake.movies[:7]] == before
    assert fake.movies[late_id] is late
    assert len(late.data) == 12 * 1600


async def test_stranded_when_app_movie_after_ours(
    device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    await device.async_show_canvas(_canvas(), Active("painted", None))
    (old,) = _own(fake, device)
    fake.add_app_movie("Later", 10)
    await device.async_show_canvas(_canvas(WARM), Active("painted", None))
    assert old in fake.movies
    assert len(_own(fake, device)) == 2
    state = MovieState.from_api(await fake.get_movies())
    assert stranded(state, set(device.store.our_movies)) == 1


async def test_memory_full_raises(device: TwinklyDevice, fake: FakeTwinkly) -> None:
    await device.async_show_canvas(_canvas(), Active("painted", None))
    fake.max_capacity -= fake.available_frames - 30
    assert fake.available_frames == 30
    spec = EffectSpec("rainbow", tuple(range(LEDS)), speed=0.44)
    assert loop_length(spec, device.fps) == 120
    movies, rt_frames, mode = list(fake.movies), len(fake.rt_frames), fake.mode
    with pytest.raises(HomeAssistantError) as err:
        await device.async_play_effect(spec, device.store.canvas, Active("custom_effect", None))
    assert err.value.translation_key == "memory_full"
    assert fake.movies == movies  # nothing deleted, not even our own movie
    assert (len(fake.rt_frames), fake.mode) == (rt_frames, mode)  # nothing touched
    assert device.store.active == Active("painted", None)


@pytest.mark.parametrize(
    "call_name",
    [
        "new_movie",  # the re-sent frames are refused: the movie stays half-created
        "upload_movie",  # the sequence completes, but under another token
    ],
)
async def test_token_stolen_midway_restarts_once(
    device: TwinklyDevice, fake: FakeTwinkly, call_name: str
) -> None:
    epoch = fake.token_epoch
    fake.steal_token_after(call_name)
    await device.async_play_effect(CHASE, device.store.canvas, Active("custom_effect", None))
    (movie,) = _own(fake, device)
    assert fake.playing is movie
    assert movie is fake.movies[-1]
    assert len(fake.movies) == 8  # the half-created movie is gone
    assert len(movie.data) == loop_length(CHASE, 11) * 1600
    assert fake.calls.count("new_movie") == 2
    assert fake.token_epoch == epoch + 1
    assert device.store.our_movies == [movie.unique_id.lower()]


async def test_preview_then_commit(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    canvas = _canvas()
    await device.async_request_preview(canvas)
    assert fake.rt_frames == []
    await _advance(hass, 0.6)
    assert fake.mode == "rt"
    assert fake.rt_frames == [canvas.to_frame("RGBW")]
    assert len(fake.movies) == 7
    await _advance(hass, 3)
    assert fake.playing.frames == 1
    assert fake.playing.data == canvas.to_frame("RGBW")
    assert device.store.active == Active("painted", None)


async def test_painting_on_postpones_commit(
    hass: HomeAssistant,
    device: TwinklyDevice,
    fake: FakeTwinkly,
    freezer: FrozenDateTimeFactory,
) -> None:
    async def tick(seconds: float) -> None:
        freezer.tick(seconds)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    await device.async_request_preview(_canvas())
    await tick(2)
    await device.async_request_preview(_canvas(WARM))
    await tick(2)
    assert len(fake.movies) == 7  # 4 s after the first change, 2 s after the last
    await tick(2)
    assert fake.calls.count("new_movie") == 1
    assert fake.playing.data == _canvas(WARM).to_frame("RGBW")


async def test_commit_leaves_movie_mode_before_deleting(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    """The app plays our movie again during a preview; the commit still replaces it."""
    await device.async_show_canvas(_canvas(), Active("painted", None))
    await device.async_request_preview(_canvas(WARM))
    await _advance(hass, 0.6)
    await fake.set_mode("movie")
    await _advance(hass, 3)
    assert fake.playing.data == _canvas(WARM).to_frame("RGBW")
    assert len(_own(fake, device)) == 1


async def test_commit_now(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    canvas = _canvas()
    await device.async_request_preview(canvas)
    await device.async_commit_now()
    assert fake.playing.data == canvas.to_frame("RGBW")
    await _advance(hass, 10)
    assert fake.calls.count("new_movie") == 1  # the commit timer was cancelled
    assert fake.rt_frames == [canvas.to_frame("RGBW")]  # only the upload bridge
    calls = len(fake.calls)
    await device.async_commit_now()  # nothing changed since
    assert len(fake.calls) == calls


async def test_commit_now_stores_a_changed_canvas(
    device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    """Painting with live off changes only the canvas; commit then stores it, once."""
    await device.async_commit_now()  # after a start, what the lights show is unknown
    assert fake.playing.data == Canvas(LEDS).to_frame("RGBW")
    device.store.set_canvas(_canvas(WARM))
    await device.async_commit_now()
    assert fake.playing.data == _canvas(WARM).to_frame("RGBW")
    assert device.store.active == Active("painted", None)
    device.store.save_look(Look("Moving", _canvas(RED), CHASE))
    await device.async_apply_look("Moving")
    calls = len(fake.calls)
    await device.async_commit_now()  # nothing painted since: the look keeps playing
    assert len(fake.calls) == calls


@pytest.mark.parametrize("drop", ["turn_off", "unload"])
async def test_dropped_commit_restores_newest_painting(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly, drop: str
) -> None:
    await device.async_show_canvas(_canvas(), Active("painted", None))
    device.store.set_canvas(_canvas(WARM))  # the card stores the painting, then previews it
    await device.async_request_preview(device.store.canvas)
    await _advance(hass, 0.6)
    if drop == "turn_off":
        await device.async_turn_off()
    else:  # HA stops; the next start makes a new device on the same store
        await device.async_shutdown()
        device = TwinklyDevice(hass, fake, device.store, device.info)
    await _advance(hass, 10)
    assert fake.calls.count("new_movie") == 1  # the commit was dropped
    await device.async_turn_on()
    assert fake.playing.data == _canvas(WARM).to_frame("RGBW")
    own = [m.unique_id.lower() for m in _own(fake, device)]
    assert own == [device.store.current_movie_uid]  # the older painting was replaced


async def test_command_cancels_pending_preview(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    await device.async_request_preview(_canvas())
    await device.async_select_builtin(1)
    await _advance(hass, 10)
    assert (fake.mode, fake.effect_id) == ("effect", 1)
    assert fake.rt_frames == []
    assert len(fake.movies) == 7


async def test_find_blinks_then_restores(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    device.store.set_canvas(Canvas(LEDS, [RED] * LEDS))
    await device.async_select_builtin(2)
    calls = len(fake.calls)
    await _while_time_passes(hass, device.async_find([0, 1]))
    assert len(fake.rt_frames) >= 10
    white, dark, dim_red = bytes([0, 255, 255, 255]), bytes(4), bytes([0, 38, 0, 0])
    assert fake.rt_frames[0][:12] == white * 2 + dim_red
    assert fake.rt_frames[1][:12] == dark * 2 + dim_red
    assert fake.calls[calls:].count("set_mode") == 2  # rt once, not per frame; then effect
    assert (fake.mode, fake.effect_id) == ("effect", 2)


async def test_find_when_off_stays_off(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    await device.async_show_canvas(_canvas(), Active("painted", None))
    await device.async_turn_off()
    await _while_time_passes(hass, device.async_find([5]))
    assert fake.rt_frames[1:]  # it did blink
    assert fake.mode == "off"


async def test_turn_off_then_on_restores_look(
    device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    device.store.save_look(Look("Warm", Canvas(LEDS, [WARM] * LEDS), None))
    await device.async_apply_look("Warm")
    movie = fake.playing
    assert movie.data == Canvas(LEDS, [WARM] * LEDS).to_frame("RGBW")
    await device.async_turn_off()
    assert fake.mode == "off"
    uploads = fake.calls.count("new_movie")
    await device.async_turn_on()
    assert fake.playing is movie
    assert fake.calls.count("new_movie") == uploads
    assert device.store.active == Active("look", "Warm")


async def test_turn_on_reuploads_when_movie_gone(
    device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    canvas = _canvas()
    await device.async_show_canvas(canvas, Active("painted", None))
    await _lose_our_movie(device, fake)
    assert len(fake.movies) == 7
    await device.async_turn_on()
    assert fake.playing.data == canvas.to_frame("RGBW")
    assert fake.playing.unique_id.lower() == device.store.current_movie_uid


async def test_restore_when_movie_gone(device: TwinklyDevice, fake: FakeTwinkly) -> None:
    """The custom effect renders again; a missing look or effect falls back to the canvas."""
    canvas = _canvas()
    device.store.set_canvas(canvas)
    device.store.set_custom_effect(CHASE)
    await device.async_play_effect(CHASE, device.store.canvas, Active("custom_effect", None))
    await _lose_our_movie(device, fake)
    await device.async_turn_on()
    assert fake.playing.data == b"".join(render(CHASE, canvas, 11))
    assert device.store.active == Active("custom_effect", None)

    device.store.set_custom_effect(None)
    await _lose_our_movie(device, fake)
    await device.async_turn_on()
    assert fake.playing.data == canvas.to_frame("RGBW")
    assert device.store.active == Active("painted", None)

    device.store.active = Active("look", "Deleted look")
    await _lose_our_movie(device, fake)
    await device.async_turn_on()
    assert fake.playing.data == canvas.to_frame("RGBW")
    assert device.store.active == Active("painted", None)


async def test_moving_effect_frames_and_fps(
    device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    spec = EffectSpec("chase", tuple(range(LEDS)), speed=0.5)
    base = _canvas(WARM)
    await device.async_play_effect(spec, base, Active("custom_effect", None))
    assert device.fps == min(11, math.floor(11.61))
    movie = fake.playing
    assert movie.frames == loop_length(spec, device.fps)
    assert movie.fps == min(11, math.floor(11.61))
    assert movie.data == b"".join(render(spec, base, device.fps))
    assert device.store.active == Active("custom_effect", None)


async def test_select_builtin_and_app_movie(
    device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    await device.async_select_builtin(3)
    assert (fake.mode, fake.effect_id) == ("effect", 3)
    assert device.store.active == Active("builtin", "3")

    await device.async_select_app_movie(2)
    assert fake.playing is fake.movies[2]
    assert device.store.active == Active("app_movie", fake.movies[2].unique_id.lower())
    await device.async_turn_off()
    await device.async_turn_on()
    assert fake.playing is fake.movies[2]


async def test_unknown_look_and_app_movie(device: TwinklyDevice, fake: FakeTwinkly) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await device.async_apply_look("Missing")
    assert err.value.translation_key == "unknown_look"
    with pytest.raises(ServiceValidationError) as err:
        await device.async_select_app_movie(99)
    assert err.value.translation_key == "unknown_effect"
    assert (fake.mode, len(fake.movies)) == ("effect", 7)


async def test_device_errors(device: TwinklyDevice, fake: FakeTwinkly) -> None:
    fake.fail_with_connection_error = True
    with pytest.raises(TwinklyConnectionError):  # the coordinator handles these itself
        await device.async_get_state()
    with pytest.raises(HomeAssistantError) as err:
        await device.async_select_builtin(1)
    assert (err.value.translation_domain, err.value.translation_key) == (
        "twinkly_canvas",
        "device_error",
    )


async def test_shutdown_cancels_timers(
    hass: HomeAssistant, device: TwinklyDevice, fake: FakeTwinkly
) -> None:
    await device.async_request_preview(_canvas())
    await device.async_shutdown()
    await _advance(hass, 10)
    assert fake.rt_frames == []
    assert len(fake.movies) == 7
