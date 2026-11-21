"""Device layer: turns what the user wants into Twinkly call sequences.

Previews and find use real-time frames; anything that must keep playing becomes one of
our own stored movies. Only movies this integration recorded as its own are deleted,
and only from the end of the movie list.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
import logging
import math
import uuid

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.event import async_call_later

from .api import TwinklyAuthError, TwinklyClient, TwinklyError
from .canvas import OFF, Canvas
from .colors import RGBW, scale
from .const import (
    DEFAULT_MAX_MOVIES,
    DOMAIN,
    FIND_SECONDS,
    LIVE_COMMIT_AFTER,
    LIVE_DEBOUNCE,
    MAX_FPS,
    MAX_FRAMES_PER_MOVIE,
    RT_TIMEOUT_SECONDS,
)
from .effects import EffectSpec, render
from .movies import DeviceMovie, MovieState, plan_upload
from .store import Active, CanvasStore, Look

_LOGGER = logging.getLogger(__name__)

_FIND_ON = (255, 255, 255, 0)
_FIND_DIM = 0.15  # the other LEDs show 15% of their canvas colour
_BLINK_SECONDS = 0.5  # one real-time frame every half second: on, off, on ...


@dataclass(frozen=True)
class DeviceInfoData:
    """What the device reports about itself."""

    mac: str
    name: str
    model: str
    firmware: str
    hardware: str
    led_count: int
    profile: str
    bytes_per_led: int
    frame_rate: float
    max_movies: int
    strings: tuple[tuple[int, int], ...]  # (first LED, length) per string

    @classmethod
    def from_api(
        cls, gestalt: dict, firmware: str, led_config: dict | None
    ) -> DeviceInfoData:
        """From gestalt, fw/version and led/config (None: one string of all LEDs)."""
        leds = gestalt["number_of_led"]
        bytes_per_led = gestalt["bytes_per_led"]
        strings = tuple(
            (s["first_led_id"], s["length"]) for s in (led_config or {}).get("strings", [])
        )
        return cls(
            mac=format_mac(gestalt["mac"]),
            name=gestalt["device_name"],
            model=gestalt.get("product_code", ""),
            firmware=firmware,
            hardware=str(gestalt.get("hardware_version", "")),
            led_count=leds,
            profile=gestalt.get("led_profile") or ("RGB" if bytes_per_led == 3 else ""),
            bytes_per_led=bytes_per_led,
            frame_rate=gestalt["frame_rate"],
            max_movies=gestalt.get("max_movies") or DEFAULT_MAX_MOVIES,
            strings=strings or ((0, leds),),
        )


@dataclass
class DeviceState:
    """One poll of the device."""

    mode: str
    brightness: int
    effect_id: int | None  # the built-in effect, in effect mode
    effects_count: int
    current_movie: DeviceMovie | None  # the playing movie, in movie mode
    movies: MovieState


class TwinklyDevice:
    """One device. Every operation runs under one lock, so calls never interleave."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: TwinklyClient,
        store: CanvasStore,
        info: DeviceInfoData,
    ) -> None:
        self.hass = hass
        self.client = client
        self.store = store
        self.info = info
        self._lock = asyncio.Lock()
        self._rt_until = 0.0  # loop time until which our last real-time frame holds rt mode
        self._preview = store.canvas
        # The canvas our last upload showed (an effect: its base); None: unknown after a start
        self._uploaded: list[RGBW] | None = None
        self._cancel_commit: CALLBACK_TYPE | None = None
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=LIVE_DEBOUNCE,
            immediate=False,
            function=self._async_send_preview,
        )

    @property
    def fps(self) -> int:
        """Movie frame rate: the device's frame rate rounded down, 1 to MAX_FPS."""
        return min(MAX_FPS, max(1, math.floor(self.info.frame_rate)))

    async def async_get_state(self) -> DeviceState:
        """Poll the device. Raises TwinklyError; the coordinator reports it."""
        async with self._lock:
            mode = (await self.client.get_mode())["mode"]
            movies = await self._movie_state()
            current = None
            if mode == "movie":  # movies/current reports a stale id in other modes
                movie_id = (await self.client.get_current_movie()).get("id")
                current = next((m for m in movies.movies if m.id == movie_id), None)
            return DeviceState(
                mode=mode,
                brightness=await self.client.get_brightness(),
                effect_id=await self.client.get_current_effect() if mode == "effect" else None,
                effects_count=(await self.client.get_effects())["effects_number"],
                current_movie=current,
                movies=movies,
            )

    async def async_show_canvas(self, canvas: Canvas, active: Active) -> None:
        """Store the canvas, then show it as a 1-frame movie of ours."""
        self._cancel_preview()
        async with self._command():
            await self._show_canvas(canvas, active)

    async def async_play_effect(
        self, spec: EffectSpec, base: Canvas, active: Active
    ) -> None:
        """Play the effect over base as a looping movie of ours."""
        self._cancel_preview()
        async with self._command():
            await self._play_effect(spec, base, active)

    async def async_request_preview(self, canvas: Canvas) -> None:
        """Show canvas live (debounced) and store it as a movie once painting pauses."""
        self._preview = canvas
        self._debouncer.async_schedule_call()
        if self._cancel_commit:
            self._cancel_commit()
        self._cancel_commit = async_call_later(
            self.hass, LIVE_COMMIT_AFTER, self._async_commit
        )

    async def async_commit_now(self) -> None:
        """Store the painting right away.

        That is a pending live preview, else the canvas if it changed since our last
        upload: painting with live off only changes the stored canvas.
        """
        if self._cancel_commit:
            await self.async_show_canvas(self._preview, Active("painted", None))
        elif self.store.canvas.pixels != self._uploaded:
            await self.async_show_canvas(self.store.canvas, Active("painted", None))

    async def async_find(self, indices: list[int]) -> None:
        """Blink the LEDs at indices, the others dimmed, then show what was shown before."""
        async with self._command():
            dim = Canvas(
                self.store.led_count,
                [scale(p, _FIND_DIM) for p in self.store.canvas.pixels],
            )
            on, off = dim.copy(), dim.copy()
            on.set(indices, _FIND_ON)
            off.set(indices, OFF)
            frames = (on.to_frame(self.info.profile), off.to_frame(self.info.profile))
            was_off = (await self.client.get_mode())["mode"] == "off"
            for i in range(round(FIND_SECONDS / _BLINK_SECONDS)):
                await self._send_rt(frames[i % 2])
                await asyncio.sleep(_BLINK_SECONDS)
            if was_off:
                await self._set_mode("off")
            else:
                await self._restore()

    async def async_restore(self) -> None:
        """Show store.active again."""
        async with self._command():
            await self._restore()

    async def async_select_builtin(self, effect_id: int) -> None:
        self._cancel_preview()
        async with self._command():
            await self.client.set_effect(effect_id)
            await self._set_mode("effect")
            self.store.active = Active("builtin", str(effect_id))

    async def async_select_app_movie(self, movie_id: int) -> None:
        self._cancel_preview()
        async with self._command():
            state = await self._movie_state()
            movie = next((m for m in state.movies if m.id == movie_id), None)
            if movie is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_effect",
                    translation_placeholders={"effect": str(movie_id)},
                )
            await self.client.set_current_movie(movie.id)
            await self._set_mode("movie")
            self.store.active = Active("app_movie", movie.unique_id)

    async def async_apply_look(self, name: str) -> None:
        """Show a saved look; its canvas becomes the canvas."""
        if (look := self.store.looks.get(name)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_look",
                translation_placeholders={"name": name},
            )
        self._cancel_preview()
        async with self._command():
            await self._show_look(look)

    async def async_turn_on(self) -> None:
        """Show what was shown before the lights went off."""
        async with self._command():
            if self.store.active.type == "off":
                self.store.active = Active("painted", None)
            await self._restore()

    async def async_turn_off(self) -> None:
        """Lights off. store.active stays, so turning on brings it back."""
        self._cancel_preview()
        async with self._command():
            await self._set_mode("off")

    async def async_set_brightness(self, percent: int) -> None:
        async with self._command():
            await self.client.set_brightness(percent)

    async def async_shutdown(self) -> None:
        """Cancel a pending live preview and its commit; nothing fires after this."""
        self._cancel_preview()
        self._debouncer.async_shutdown()

    @asynccontextmanager
    async def _command(self) -> AsyncIterator[None]:
        """Run under the device lock; device errors become HomeAssistantError."""
        async with self._lock:
            try:
                yield
            except TwinklyError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="device_error",
                    translation_placeholders={"error": str(err)},
                ) from err

    @callback
    def _cancel_preview(self) -> None:
        """Drop a pending live preview and its commit: a newer command wins."""
        self._debouncer.async_cancel()
        if self._cancel_commit:
            self._cancel_commit()
            self._cancel_commit = None
            # our movie is older than the painting: restore uploads the canvas instead
            self.store.current_movie_uid = None

    async def _async_send_preview(self) -> None:
        async with self._lock:
            try:
                await self._send_rt(self._preview.to_frame(self.info.profile))
            except TwinklyError as err:
                _LOGGER.debug("Live preview frame not shown: %s", err)

    async def _async_commit(self, _now: datetime) -> None:
        self._cancel_commit = None
        try:
            await self.async_show_canvas(self._preview, Active("painted", None))
        except HomeAssistantError as err:
            _LOGGER.warning("Could not store the painting on the lights: %s", err)

    async def _show_canvas(self, canvas: Canvas, active: Active) -> None:
        self.store.set_canvas(canvas)
        await self._upload([canvas.to_frame(self.info.profile)], self.fps)
        self._uploaded = list(canvas.pixels)
        self.store.active = active

    async def _play_effect(self, spec: EffectSpec, base: Canvas, active: Active) -> None:
        frames = await self.hass.async_add_executor_job(
            render, spec, base, self.fps, self.info.profile, MAX_FRAMES_PER_MOVIE
        )
        await self._upload(frames, self.fps)
        self._uploaded = list(base.pixels)
        self.store.active = active

    async def _show_look(self, look: Look) -> None:
        active = Active("look", look.name)
        if look.effect is None:
            await self._show_canvas(look.canvas, active)
        else:
            self.store.set_canvas(look.canvas)
            await self._play_effect(look.effect, look.canvas, active)

    async def _restore(self) -> None:
        active = self.store.active
        if active.type == "off":
            await self._set_mode("off")
            return
        if active.type == "builtin":
            await self.client.set_effect(int(active.ref))  # type: ignore[arg-type]
            await self._set_mode("effect")
            return
        uid = active.ref if active.type == "app_movie" else self.store.current_movie_uid
        state = await self._movie_state()
        if movie := next((m for m in state.movies if m.unique_id == uid), None):
            await self.client.set_current_movie(movie.id)
            await self._set_mode("movie")
        elif active.type == "look" and active.ref in self.store.looks:
            await self._show_look(self.store.looks[active.ref])
        elif active.type == "custom_effect" and self.store.custom_effect:
            await self._play_effect(self.store.custom_effect, self.store.canvas, active)
        else:  # the canvas itself, or what was shown is gone
            await self._show_canvas(self.store.canvas, Active("painted", None))

    async def _movie_state(self) -> MovieState:
        return MovieState.from_api(await self.client.get_movies(), self.info.max_movies)

    async def _upload(self, frames: list[bytes], fps: int) -> None:
        """Store frames as our newest movie and play it.

        If another client logs in midway (the token epoch moves, or the token is refused
        even after a new login), the whole sequence runs once more from a fresh movie list.
        """
        for attempt in (0, 1):
            state = await self._movie_state()  # a login during this read changes nothing yet
            epoch = self.client.token_epoch
            try:
                await self._store_movie(state, frames, fps)
            except TwinklyError as err:
                lost_token = (
                    isinstance(err, TwinklyAuthError) or self.client.token_epoch != epoch
                )
                if attempt or not lost_token:
                    raise
            else:
                if attempt or self.client.token_epoch == epoch:
                    return

    async def _store_movie(
        self, state: MovieState, frames: list[bytes], fps: int
    ) -> None:
        plan = plan_upload(state, set(self.store.our_movies), len(frames))
        if not plan.fits:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key=plan.reason)
        # Bridge: the first frame shows at once. Entering rt also stops a playing movie,
        # which the device refuses to delete (DELETE_NEEDS_MODE_CHANGE).
        self._rt_until = 0.0
        await self._send_rt(frames[0])
        for listed_uid in plan.delete:
            await self.client.delete_movie(listed_uid)
            self.store.remove_our_movie(listed_uid)
        uid = str(uuid.uuid4())
        self.store.add_our_movie(uid)  # before it exists: no movie of ours goes untracked
        movie = await self.client.new_movie(
            self.store.next_movie_name(),
            uid,
            len(frames),
            fps,
            self.info.led_count,
            f"{self.info.profile.lower()}_raw",
        )
        await self.client.upload_movie(b"".join(frames))
        await self.client.set_current_movie(movie["id"])
        await self._set_mode("movie")
        self.store.current_movie_uid = uid

    async def _send_rt(self, frame: bytes) -> None:
        """Show one real-time frame; enters rt mode unless our last frame still holds it."""
        now = self.hass.loop.time()
        if now >= self._rt_until:
            await self.client.set_mode("rt")
        await self.client.send_rt_frame(frame)
        self._rt_until = now + RT_TIMEOUT_SECONDS

    async def _set_mode(self, mode: str) -> None:
        self._rt_until = 0.0  # any other mode ends rt
        await self.client.set_mode(mode)
