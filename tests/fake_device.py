"""A simulated Twinkly device with the same async methods as TwinklyClient.

It follows the behaviour measured on a real device: movie storage costs frames plus
MOVIE_OVERHEAD_FRAMES per movie, only the last movie can be deleted (and not while it
plays), unique_ids are listed upper-cased and DELETE needs the id exactly as listed,
creating a movie changes neither the mode nor the current movie, and a real-time frame
outside rt mode is refused with code 1103.

advance() is the clock helper the tests share.
"""

from __future__ import annotations

import functools
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import async_fire_time_changed

from custom_components.twinkly_canvas.api import (
    TwinklyConnectionError,
    TwinklyResponseError,
)
from custom_components.twinkly_canvas.const import (
    DELETE_NEEDS_MODE_CHANGE,
    MOVIE_OVERHEAD_FRAMES,
)

LEDS = 400
EFFECTS = 5
APP_MOVIE_FRAMES = (59, 8, 60, 116, 61, 32, 48)
MODES = ("off", "color", "demo", "effect", "movie", "playlist", "rt")
FIRMWARE = "2.8.18"
GESTALT = {
    "product_code": "TEST400",
    "hardware_version": "100",
    "device_name": "Test Tree",
    "mac": "aa:bb:cc:00:11:22",
    "uuid": "00000000-0000-4000-8000-000000000001",
    "number_of_led": LEDS,
    "led_profile": "RGBW",
    "bytes_per_led": 4,
    "frame_rate": 11.61,
    "movie_capacity": 2536,
    "max_movies": 55,
    "compatibility_mode": "enabled",
    "code": 1000,
}


async def advance(hass: HomeAssistant, seconds: float) -> None:
    """Run the timers due within the next seconds; not cumulative, the clock stays."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


@dataclass(eq=False)
class FakeMovie:
    name: str
    unique_id: str  # as the device lists it: upper-cased
    frames: int
    fps: int
    data: bytes = b""  # stays empty until movies/full delivers the frames


def _device_call(auth: bool = True) -> Callable[[Callable], Callable]:
    """Record the call; fail it on request; log in again like the client does after a 401."""

    def decorate(func: Callable) -> Callable:
        @functools.wraps(func)
        async def call(fake: FakeTwinkly, *args: Any, **kwargs: Any) -> Any:
            fake.calls.append(func.__name__)
            if fake.fail_with_connection_error:
                raise TwinklyConnectionError(f"{func.__name__}: no answer")
            if auth and not fake.logged_in:
                await fake.login()
            result = await func(fake, *args, **kwargs)
            if fake.steal_after and fake.steal_after[0] == func.__name__:
                fake.steal_after.pop(0)
                fake.logged_in = False  # another client logged in: our token is dead
            return result

        return call

    return decorate


class FakeTwinkly:
    """One simulated device: modes, 5 effects, stored movies, brightness, real-time frames."""

    def __init__(self, profile: str = "RGBW") -> None:
        self.profile = profile  # "RGB": lights without a white channel
        self.frame_bytes = LEDS * len(profile)  # one byte per channel
        self.mode = "effect"
        self.effect_id = 0
        self.brightness = 100
        self.max_capacity = 2536
        self.max_movies = 55
        self.movies: list[FakeMovie] = []
        self.current: FakeMovie | None = None
        self.rt_frames: list[bytes] = []  # the real-time frames shown
        self.dropped_rt_frames = 0  # sent outside rt mode: not shown
        self.calls: list[str] = []  # method names, in call order
        self.token_epoch = 0
        self.logged_in = False
        self.fail_with_connection_error = False
        self.fail_upload = False  # movies/full answers an error
        self.omit_new_movie_id = False  # movies/new answers without "id"
        self.steal_after: list[str] = []
        self._pending: FakeMovie | None = (
            None  # created by movies/new, waiting for movies/full
        )
        for n, frames in enumerate(APP_MOVIE_FRAMES, 1):
            self.add_app_movie(f"App movie {n}", frames)

    # Test hooks

    def steal_token_after(self, *call_names: str) -> None:
        """Another client logs in right after the next call of each name, in turn."""
        self.steal_after = list(call_names)

    def add_app_movie(self, name: str, frames: int) -> FakeMovie:
        """A movie stored by the Twinkly app, appended to the list."""
        movie = FakeMovie(
            name,
            str(uuid.uuid4()).upper(),
            frames,
            11,
            bytes(frames * self.frame_bytes),
        )
        self.movies.append(movie)
        return movie

    @property
    def available_frames(self) -> int:
        return self.max_capacity - sum(
            m.frames + MOVIE_OVERHEAD_FRAMES for m in self.movies
        )

    @property
    def playing(self) -> FakeMovie | None:
        return self.current if self.mode == "movie" else None

    # TwinklyClient methods

    @_device_call(auth=False)
    async def login(self) -> None:
        self.token_epoch += 1
        self.logged_in = True
        self._pending = (
            None  # a new session: frames for an earlier movies/new are refused
        )

    @_device_call(auth=False)
    async def gestalt(self) -> dict:
        return {
            **GESTALT,
            "led_profile": self.profile,
            "bytes_per_led": len(self.profile),
        }

    @_device_call(auth=False)
    async def firmware_version(self) -> str:
        return FIRMWARE

    @_device_call()
    async def led_config(self) -> dict:
        return {
            "strings": [
                {"first_led_id": 0, "length": LEDS // 2},
                {"first_led_id": LEDS // 2, "length": LEDS // 2},
            ],
            "code": 1000,
        }

    @_device_call()
    async def get_mode(self) -> dict:
        return {"mode": self.mode, "code": 1000}

    @_device_call()
    async def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise TwinklyResponseError(1101, f"Unknown mode {mode!r}")
        if mode == "movie" and self.current is None:
            raise TwinklyResponseError(1104, "No movie to play")
        self.mode = mode

    @_device_call()
    async def get_brightness(self) -> int:
        return self.brightness

    @_device_call()
    async def set_brightness(self, value: int) -> None:
        self.brightness = value

    @_device_call()
    async def get_effects(self) -> dict:
        return {
            "effects_number": EFFECTS,
            "unique_ids": [
                f"00000000-0000-0000-0000-00000000000{n}" for n in range(1, EFFECTS + 1)
            ],
            "code": 1000,
        }

    @_device_call()
    async def get_current_effect(self) -> int:
        return self.effect_id

    @_device_call()
    async def set_effect(self, effect_id: int) -> None:
        if not 0 <= effect_id < EFFECTS:
            raise TwinklyResponseError(1101, f"No effect {effect_id}")
        self.effect_id = effect_id

    @_device_call()
    async def get_movies(self) -> dict:
        return {
            "movies": [
                {
                    "id": i,
                    "name": m.name,
                    "unique_id": m.unique_id,
                    "descriptor_type": f"{self.profile.lower()}_raw",
                    "leds_per_frame": LEDS,
                    "frames_number": m.frames,
                    "fps": m.fps,
                }
                for i, m in enumerate(self.movies)
            ],
            "available_frames": self.available_frames,
            "max_capacity": self.max_capacity,
            "max": self.max_movies,
            "code": 1000,
        }

    @_device_call()
    async def get_current_movie(self) -> dict:
        if self.current is None:
            return {"code": 1000}
        return {
            "id": self.movies.index(self.current),
            "unique_id": self.current.unique_id,
            "name": self.current.name,
            "code": 1000,
        }

    @_device_call()
    async def set_current_movie(self, movie_id: int) -> None:
        if not 0 <= movie_id < len(self.movies):
            raise TwinklyResponseError(1101, f"No movie {movie_id}")
        self.current = self.movies[movie_id]

    @_device_call()
    async def new_movie(
        self,
        name: str,
        unique_id: str,
        frames: int,
        fps: int,
        leds: int,
        descriptor: str,
    ) -> dict:
        try:
            uuid.UUID(unique_id)
        except ValueError as err:
            raise TwinklyResponseError(412, "unique_id is not a UUID") from err
        if leds != LEDS or descriptor != f"{self.profile.lower()}_raw":
            raise TwinklyResponseError(1101, f"Wrong format {leds} x {descriptor!r}")
        if (
            len(self.movies) >= self.max_movies
            or frames + MOVIE_OVERHEAD_FRAMES > self.available_frames
        ):
            raise TwinklyResponseError(1101, "No room for another movie")
        movie = FakeMovie(name, unique_id.upper(), frames, fps)
        self.movies.append(movie)
        self._pending = movie
        reply = {"id": len(self.movies) - 1, "unique_id": movie.unique_id, "code": 1000}
        if self.omit_new_movie_id:
            del reply["id"]
        return reply

    @_device_call()
    async def upload_movie(self, data: bytes) -> dict:
        movie, self._pending = self._pending, None
        if movie is None:
            raise TwinklyResponseError(1001, "movies/full without movies/new")
        if self.fail_upload:  # the movie stays half-created, like after a lost token
            raise TwinklyResponseError(1102, "Upload failed")
        if len(data) != movie.frames * self.frame_bytes:
            raise TwinklyResponseError(1106, "Frame data does not match frames_number")
        movie.data = data
        return {"frames_number": movie.frames, "code": 1000}

    @_device_call()
    async def delete_movie(self, unique_id: str) -> None:
        if not re.fullmatch(
            r"[0-9A-Za-z-]+", unique_id
        ):  # the client refuses these too
            raise ValueError(f"Refusing to delete movie {unique_id!r}")
        if not self.movies or self.movies[-1].unique_id != unique_id:
            raise TwinklyResponseError(1101, "Only the last movie, by its listed id")
        movie = self.movies[-1]
        if DELETE_NEEDS_MODE_CHANGE and self.playing is movie:
            raise TwinklyResponseError(412, "The movie is playing")
        self.movies.pop()
        if self.current is movie:
            self.current = None

    @_device_call()
    async def send_rt_frame(self, data: bytes) -> None:
        if len(data) != self.frame_bytes:
            raise TwinklyResponseError(1106, "Wrong frame size")
        if self.mode != "rt":  # measured: HTTP 200 with code 1103, nothing shows
            self.dropped_rt_frames += 1
            raise TwinklyResponseError(1103, "Not in real-time mode")
        self.rt_frames.append(data)
