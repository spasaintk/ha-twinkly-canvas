"""Device layer: turns what the user wants into Twinkly call sequences.

Previews and find use real-time frames; anything that must keep playing becomes one of
our own stored movies. Only movies this integration recorded as its own are deleted,
and only from the end of the movie list.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.event import async_call_later

from .api import TwinklyAuthError, TwinklyClient, TwinklyError, TwinklyResponseError
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
    PREVIOUS_PAINTING,
    RT_TIMEOUT_SECONDS,
)
from .effects import EffectSpec, render
from .movies import DeviceMovie, MovieState, plan_upload
from .store import Active, CanvasStore, Look

_LOGGER = logging.getLogger(__name__)

_FIND_ON = (255, 255, 255, 0)
_FIND_DIM = 0.15  # the other LEDs show 15% of their canvas colour
_BLINK_SECONDS = 0.5  # one real-time frame every half second: on, off, on ...
_NOT_IN_RT = 1103  # the device's code for a real-time frame outside rt mode


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
            (s["first_led_id"], s["length"])
            for s in (led_config or {}).get("strings", [])
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
    changes: int  # TwinklyDevice's count of our changes when this poll ran


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
        # loop time until which our last real-time frame holds rt mode
        self._rt_until = 0.0
        # The canvas our last upload showed standing still; None: unknown, or a loop
        self._uploaded: list[RGBW] | None = None
        self._notification_id = f"{DOMAIN}_commit_failed_{store.entry_id}"
        # Set from a live paint until its commit runs under the lock or a command drops it
        self._cancel_commit: CALLBACK_TYPE | None = None
        self._closed = False  # shut down: no timer may start again
        # How often we changed what plays; a poll older than our last change is stale
        self._changes = 0
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
            if mode != "rt":
                # something else ended rt: our next frame enters it again
                self._rt_until = 0.0
            movies = await self._movie_state()
            current = await self._playing_movie(movies) if mode == "movie" else None
            return DeviceState(
                mode=mode,
                brightness=await self.client.get_brightness(),
                effect_id=await self.client.get_current_effect()
                if mode == "effect"
                else None,
                effects_count=(await self.client.get_effects())["effects_number"],
                current_movie=current,
                movies=movies,
                changes=self._changes,
            )

    def playing(self, state: DeviceState) -> Active:
        """What plays: the polled mode first, store.active where the poll cannot tell.

        It cannot tell in rt and off, for our own movies, and once we changed what plays
        after it ran.
        """
        if state.changes == self._changes:
            if state.mode == "effect":
                return Active("builtin", str(state.effect_id))
            if state.mode in ("playlist", "color", "demo"):
                return Active("device_mode", state.mode)
            movie = state.current_movie  # set in movie mode only
            if movie and movie.unique_id not in self.store.our_movies:
                return Active("app_movie", movie.unique_id)
        return self.store.active

    async def async_show_canvas(
        self, canvas: Canvas, active: Active, keep_painting: bool = False
    ) -> None:
        """Store the canvas, then show it as a 1-frame movie of ours.

        keep_painting: an unsaved painting it replaces becomes the look PREVIOUS_PAINTING.
        """
        self._cancel_preview()
        async with self._command():
            if keep_painting:
                self._keep_painting()
            await self._show_canvas(canvas, active)

    async def async_show_painting(self) -> None:
        """Show the canvas as it is once the lock is ours (strokes made meanwhile too)."""
        self._cancel_preview()
        async with self._command():
            await self._show_canvas(self.store.canvas, Active("painted", None))

    async def async_play_effect(
        self, spec: EffectSpec, base: Canvas, active: Active
    ) -> None:
        """Play the effect over base as a looping movie of ours."""
        self._cancel_preview()
        async with self._command():
            await self._play_effect(spec, base, active)

    async def async_request_preview(self) -> None:
        """Show the canvas live (debounced) and store it as a movie once painting pauses."""
        if self._closed:
            return
        self._debouncer.async_schedule_call()
        if self._cancel_commit:
            self._cancel_commit()
        self._cancel_commit = async_call_later(
            self.hass, LIVE_COMMIT_AFTER, self._async_commit
        )

    async def async_commit_now(self) -> None:
        """Show the painting (the canvas) right away, uploading it unless we already did.

        Painting with live off only changes the stored canvas.
        """
        async with self._command():  # the canvas as it is once the lock is ours
            if self._cancel_preview() or self.store.canvas.pixels != self._uploaded:
                await self._show_canvas(self.store.canvas, Active("painted", None))
            else:  # our movie of it plays again (restore uploads it if it is gone)
                self.store.active = Active("painted", None)
                await self._restore()

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
            # Off or on decides how Find ends: an unreadable mode stops it here
            mode = (await self.client.get_mode())["mode"]
            await self._record_playing(mode)
            for i in range(round(FIND_SECONDS / _BLINK_SECONDS)):
                await self._send_rt(frames[i % 2])
                await asyncio.sleep(_BLINK_SECONDS)
            if mode == "off":
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
        """Play a movie of the Twinkly app; our own movies are refused like unknown ids."""
        async with self._command():
            state = await self._movie_state()
            movie = next((m for m in state.movies if m.id == movie_id), None)
            if movie is None or movie.unique_id in self.store.our_movies:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_effect",
                    translation_placeholders={"effect": str(movie_id)},
                )
            self._cancel_preview()  # only now: a refused id keeps a pending painting
            await self.client.set_current_movie(movie.id)
            await self._set_mode("movie")
            self.store.active = Active("app_movie", movie.unique_id)

    async def async_apply_look(self, name: str) -> None:
        """Show a saved look, named ignoring case; its canvas becomes the canvas."""
        self._require_look(name)  # refuse before a pending painting is dropped
        self._cancel_preview()
        async with self._command():
            # Looked up again once the lock is ours: a command before us may have
            # rewritten the look (the Previous painting slot) or deleted it
            await self._show_look(self._require_look(name))

    def _require_look(self, name: str) -> Look:
        if (look := self.store.find_look(name)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_look",
                translation_placeholders={"name": name},
            )
        return look

    async def async_turn_on(self) -> None:
        """Show store.active again if the lights are off; lights that are on stay as they are."""
        async with self._command():
            if (await self.client.get_mode())["mode"] != "off":
                return
            if self.store.active.type == "off":
                self.store.active = Active("painted", None)
            await self._restore()

    async def async_turn_off(self) -> None:
        """Lights off; store.active names what turning on brings back."""
        painting = self._cancel_preview()
        async with self._command():
            if not painting:  # a dropped live painting is newer than what plays
                await self._record_playing()
            await self._set_mode("off")

    async def async_set_brightness(self, percent: int) -> None:
        async with self._command():
            await self.client.set_brightness(percent)

    async def async_stop(self) -> None:
        """HA stops: store a pending live painting at once (10 s at most), then shut down.

        If that fails or takes too long, the painting stays active without a movie of
        its own, so the next start uploads it.
        """
        self._closed = True
        if self._cancel_commit:
            try:
                async with asyncio.timeout(10):
                    await self._async_commit()
            except TimeoutError:
                _LOGGER.warning("Storing the painting took too long; HA stops anyway")
        await self.async_shutdown()

    async def async_shutdown(self) -> None:
        """Drop a pending live preview: nothing fires after this.

        A command in flight finishes first.
        """
        self._closed = True
        self._cancel_preview()
        self._debouncer.async_shutdown()
        # so a store flush after this holds the command's changes
        async with self._lock:
            pass

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
    def _cancel_preview(self) -> bool:
        """Drop a pending live preview and its commit: a newer command wins.

        True when a commit was pending, its timer running or fired and waiting for the lock.
        The painting was then the newest thing on the lights, so it becomes active without
        a movie of its own: restore uploads it. A caller that shows something else sets
        active itself afterwards.
        """
        self._debouncer.async_cancel()
        if not self._cancel_commit:
            return False
        self._cancel_commit()  # does nothing once the timer fired
        self._cancel_commit = None
        self.store.current_movie_uid = None
        self.store.active = Active("painted", None)
        return True

    async def _async_send_preview(self) -> None:
        async with self._lock:
            # a command dropped this preview while it waited
            if not self._cancel_commit:
                return
            try:
                await self._send_rt(self.store.canvas.to_frame(self.info.profile))
            except TwinklyError as err:
                _LOGGER.debug("Live preview frame not shown: %s", err)

    async def _async_commit(self, _now: datetime | None = None) -> None:
        try:
            async with self._command():
                # False: a command dropped it while it waited
                if self._cancel_preview():
                    await self._show_canvas(self.store.canvas, Active("painted", None))
        except HomeAssistantError as err:
            _LOGGER.warning("Could not store the painting on the lights: %s", err)
            # the next successful upload dismisses it (_store_movie)
            persistent_notification.async_create(
                self.hass,
                f"Could not store the painting on {self.info.name}: {err}",
                title="Twinkly Canvas",
                notification_id=self._notification_id,
            )

    async def _show_canvas(self, canvas: Canvas, active: Active) -> None:
        self.store.set_canvas(canvas)
        self.store.current_movie_uid = None  # set again once the upload worked
        await self._upload([canvas.to_frame(self.info.profile)], self.fps)
        self._uploaded = list(canvas.pixels)
        self.store.active = active

    async def _play_effect(
        self, spec: EffectSpec, base: Canvas, active: Active
    ) -> None:
        frames = await self.hass.async_add_executor_job(
            render, spec, base, self.fps, self.info.profile, MAX_FRAMES_PER_MOVIE
        )
        self.store.current_movie_uid = None  # set again once the upload worked
        await self._upload(frames, self.fps)
        self._uploaded = None  # a loop, not the canvas standing still
        self.store.active = active

    async def _show_look(self, look: Look) -> None:
        # Showing the slot over an unsaved painting swaps the two: its canvas is unsaved now
        if self._keep_painting() and look.name.lower() == PREVIOUS_PAINTING.lower():
            await self._show_canvas(look.canvas, Active("painted", None))
            return
        active = Active("look", look.name)
        if look.effect is None:
            await self._show_canvas(look.canvas, active)
        else:
            self.store.set_canvas(look.canvas)
            await self._play_effect(look.effect, look.canvas, active)

    def _keep_painting(self) -> bool:
        """Before a look or a colour replaces the canvas: keep an unsaved painting.

        That is a canvas of two colours or more that no look holds (a moving look: its
        canvas). It becomes the look PREVIOUS_PAINTING, replacing the one before.
        """
        canvas = self.store.canvas
        if canvas.uniform_color() is not None or any(
            look.canvas.pixels == canvas.pixels for look in self.store.looks.values()
        ):
            return False
        self.store.save_look(Look(PREVIOUS_PAINTING, canvas, None))
        return True

    async def _restore(self) -> None:
        active = self.store.active
        if active.type == "off":
            await self._set_mode("off")
            return
        if active.type == "builtin":
            await self.client.set_effect(int(active.ref))  # type: ignore[arg-type]
            await self._set_mode("effect")
            return
        if active.type == "device_mode":
            await self._set_mode(active.ref)  # type: ignore[arg-type]
            return
        uid = active.ref if active.type == "app_movie" else self.store.current_movie_uid
        state = await self._movie_state()
        if movie := next((m for m in state.movies if m.unique_id == uid), None):
            await self.client.set_current_movie(movie.id)
            await self._set_mode("movie")
        elif active.type == "look" and (look := self.store.find_look(active.ref or "")):
            await self._show_look(look)
        elif active.type == "custom_effect" and self.store.custom_effect:
            await self._play_effect(self.store.custom_effect, self.store.canvas, active)
        else:  # the canvas itself, or what was shown is gone
            await self._show_canvas(self.store.canvas, Active("painted", None))

    async def _record_playing(self, mode: str | None = None) -> None:
        """Let store.active name what plays when it is not ours, so it can come back.

        Best effort: a device error leaves store.active as it is. The mode is read when
        not given.
        """
        try:
            if mode is None:
                mode = (await self.client.get_mode())["mode"]
            if mode == "effect":
                self.store.active = Active(
                    "builtin", str(await self.client.get_current_effect())
                )
            elif mode == "movie":
                movie = await self._playing_movie(await self._movie_state())
                if movie and movie.unique_id not in self.store.our_movies:
                    self.store.active = Active("app_movie", movie.unique_id)
            elif mode in ("playlist", "color", "demo"):
                self.store.active = Active("device_mode", mode)
        except TwinklyError as err:
            _LOGGER.debug("Could not read what plays: %s", err)

    async def _playing_movie(self, movies: MovieState) -> DeviceMovie | None:
        """The movie movies/current names; in movie mode only (a stale id in other modes)."""
        movie_id = (await self.client.get_current_movie()).get("id")
        return next((m for m in movies.movies if m.id == movie_id), None)

    async def _movie_state(self) -> MovieState:
        data = await self.client.get_movies()
        try:
            return MovieState.from_api(data, self.info.max_movies)
        except (KeyError, TypeError, ValueError) as err:  # a malformed movie entry
            raise TwinklyResponseError(
                None, "Twinkly sent an unexpected movie list"
            ) from err

    async def _upload(self, frames: list[bytes], fps: int) -> None:
        """Store frames as our newest movie and play it.

        If another client logs in midway (the token epoch moves, or the token is refused
        even after a new login), the whole sequence runs once more from a fresh movie list.
        """
        for attempt in (0, 1):
            # a login during this read changes nothing yet
            state = await self._movie_state()
            epoch = self.client.token_epoch
            try:
                await self._store_movie(state, frames, fps)
            except TwinklyError as err:
                lost_token = (
                    isinstance(err, TwinklyAuthError)
                    or self.client.token_epoch != epoch
                )
                if attempt or not lost_token:
                    raise
                _LOGGER.debug("Token lost during an upload, starting it again: %s", err)
            else:
                if attempt or self.client.token_epoch == epoch:
                    return

    async def _store_movie(
        self, state: MovieState, frames: list[bytes], fps: int
    ) -> None:
        plan = plan_upload(state, set(self.store.our_movies), len(frames))
        if not plan.fits:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=plan.reason
            )
        # Bridge: the first frame shows at once. Entering rt also stops a playing movie,
        # which the device refuses to delete (DELETE_NEEDS_MODE_CHANGE).
        self._rt_until = 0.0
        await self._send_rt(frames[0])
        for listed_uid in plan.delete:
            await self.client.delete_movie(listed_uid)
            self.store.remove_our_movie(listed_uid)
        uid = str(uuid.uuid4())
        # before it exists: no movie of ours goes untracked
        self.store.add_our_movie(uid)
        reply = await self.client.new_movie(
            self.store.next_movie_name(),
            uid,
            len(frames),
            fps,
            self.info.led_count,
            f"{self.info.profile.lower()}_raw",
        )
        # must directly follow movies/new
        await self.client.upload_movie(b"".join(frames))
        if (movie_id := reply.get("id")) is None:  # find our movie by its uid instead
            listed = await self._movie_state()
            movie_id = next((m.id for m in listed.movies if m.unique_id == uid), None)
            if movie_id is None:
                raise TwinklyResponseError(
                    None, "Twinkly sent no id for the new movie and does not list it"
                )
        await self.client.set_current_movie(movie_id)
        await self._set_mode("movie")
        self.store.current_movie_uid = uid
        persistent_notification.async_dismiss(self.hass, self._notification_id)

    async def _send_rt(self, frame: bytes) -> None:
        """Show one real-time frame; enters rt mode unless our last frame still holds it.

        The device refuses a frame outside rt mode, however rt ended (the app, a timer):
        the frame then enters rt and goes once more.
        """
        self._changes += 1
        now = self.hass.loop.time()
        if now >= self._rt_until:
            await self.client.set_mode("rt")
        try:
            await self.client.send_rt_frame(frame)
        except TwinklyResponseError as err:
            if err.code != _NOT_IN_RT:
                raise
            await self.client.set_mode("rt")
            await self.client.send_rt_frame(frame)
        self._rt_until = now + RT_TIMEOUT_SECONDS

    async def _set_mode(self, mode: str) -> None:
        self._rt_until = 0.0  # any other mode ends rt
        self._changes += 1
        await self.client.set_mode(mode)
