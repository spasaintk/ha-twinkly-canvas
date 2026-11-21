"""Websocket commands for the canvas card.

Every command names a light of this integration. LED lists hold 0-based indices,
colours are [r, g, b, w]. The commands reuse the light's actions and the device layer.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.components.light.const import DATA_COMPONENT as LIGHT_ENTITIES
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .canvas import Canvas
from .colors import NAMED_COLORS
from .const import DOMAIN, PREFIX_BUILTIN
from .effects import EFFECT_KINDS, EffectSpec, render
from .light import TwinklyCanvasLight
from .movies import stranded
from .selection import check_leds, format_selection

PREVIEW_FRAMES = 120  # the card's preview loop; a movie on the lights takes up to 240

_NAME = {vol.Required("name"): str}
_SPEC = {
    vol.Required("spec"): {
        vol.Required("kind"): vol.In(EFFECT_KINDS),
        vol.Required("leds"): vol.Any(str, [int]),  # selection text or 0-based indices
        vol.Required("colors"): vol.All(list, vol.Length(max=3)),
        vol.Required("speed"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Required("keep_others"): bool,
    }
}

type _Command = Callable[[TwinklyCanvasLight, dict[str, Any]], Awaitable[Any]]


def _command(
    name: str, schema: dict | None = None, policy: str = POLICY_CONTROL
) -> Callable[[_Command], websocket_api.WebSocketCommandHandler]:
    """A command on one light; its result, or invalid_format / device_error.

    The user needs the policy's permission on the light: control, or read for state.
    """

    def decorate(func: _Command) -> websocket_api.WebSocketCommandHandler:
        @websocket_api.websocket_command(
            {
                vol.Required("type"): f"{DOMAIN}/{name}",
                vol.Required("entity_id"): cv.entity_id,
                **(schema or {}),
            }
        )
        @websocket_api.async_response
        async def handler(
            hass: HomeAssistant,
            connection: websocket_api.ActiveConnection,
            msg: dict[str, Any],
        ) -> None:
            entity_id = msg["entity_id"]
            # HA checks this permission for the light's actions; the card must not skip it
            if not connection.user.permissions.check_entity(entity_id, policy):
                raise Unauthorized(entity_id=entity_id)
            entities = hass.data.get(LIGHT_ENTITIES)
            light = entities.get_entity(entity_id) if entities else None
            if not isinstance(light, TwinklyCanvasLight):
                connection.send_error(
                    msg["id"],
                    websocket_api.ERR_NOT_FOUND,
                    f"{entity_id} is not a Twinkly Canvas light",
                )
                return
            try:
                result = await func(light, msg)
            except (ServiceValidationError, ValueError, KeyError) as err:
                connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
            except HomeAssistantError as err:
                connection.send_error(msg["id"], "device_error", str(err))
            else:
                connection.send_result(msg["id"], result)

        return handler

    return decorate


def _text(leds: str | list[int]) -> str:
    """Selection text for the light's actions; a list holds 0-based indices."""
    # shortcut: a list whose text runs past 10000 characters is refused (scattered picks
    # on thousands of LEDs); check the indices directly if such devices turn up.
    return leds if isinstance(leds, str) else format_selection(leds)


def _effect_args(spec: dict[str, Any]) -> dict[str, Any]:
    """The spec as play_effect arguments."""
    return {**spec, "leds": _text(spec["leds"])}


def _require_look(light: TwinklyCanvasLight, name: str) -> None:
    if light.store.find_look(name) is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_look",
            translation_placeholders={"name": name},
        )


def _preview(spec: EffectSpec, base: Canvas, fps: int) -> list[str]:
    """The loop as base64 frames of R,G,B,W per LED (Canvas.to_b64's order)."""
    frames = []
    for wrgb in render(spec, base, fps, profile="RGBW", max_frames=PREVIEW_FRAMES):
        rgbw = bytearray(len(wrgb))
        rgbw[0::4] = wrgb[1::4]  # R
        rgbw[1::4] = wrgb[2::4]  # G
        rgbw[2::4] = wrgb[3::4]  # B
        rgbw[3::4] = wrgb[0::4]  # W
        frames.append(base64.b64encode(rgbw).decode())
    return frames


@_command("state", policy=POLICY_READ)
async def _state(light: TwinklyCanvasLight, msg: dict[str, Any]) -> dict[str, Any]:
    """Everything the card shows; the device part is from the last poll."""
    store, info, state = light.store, light.device.info, light.coordinator.data
    movies = state.movies
    used = movies.max_capacity - movies.available_frames
    return {
        "led_count": store.led_count,
        "strings": info.strings,
        "profile": info.profile,
        "canvas": store.canvas.to_b64(),
        "groups": store.groups,
        "looks": [
            {
                "name": look.name,
                "kind": look.kind,
                "canvas": look.canvas.to_b64(),
                "effect": look.effect.to_dict() if look.effect else None,
            }
            for look in sorted(store.looks.values(), key=lambda look: look.name.casefold())
        ],
        "custom_effect": store.custom_effect.to_dict() if store.custom_effect else None,
        "builtins": state.effects_count,
        "app_movies": [  # unique_id: the card marks the one active.ref names
            {"id": m.id, "name": m.name, "frames": m.frames, "unique_id": m.unique_id}
            for m in movies.movies
            if m.unique_id not in store.our_movies
        ],
        "memory": {
            "used_pct": 100 * used / movies.max_capacity if movies.max_capacity > 0 else 0,
            "available_frames": movies.available_frames,
            "max_capacity": movies.max_capacity,
            "movies": len(movies.movies),
            "max_movies": movies.max_movies,
            "stranded": stranded(movies, set(store.our_movies)),
        },
        "active": asdict(store.active),
        "mode": state.mode,
        "colors": NAMED_COLORS,
        "effects": EFFECT_KINDS,
    }


@_command(
    "paint",
    {
        vol.Required("leds"): [int],
        vol.Required("color"): [int],
        vol.Optional("live", default=True): bool,
    },
)
async def _paint(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    """Paint LEDs on the canvas; live, the lights show it within a second."""
    check_leds(msg["leds"], light.store.led_count)  # named from 1, like a selection
    canvas = light.store.canvas.copy()
    canvas.set(msg["leds"], msg["color"])  # checks the colour
    light.store.set_canvas(canvas)
    if msg["live"]:
        await light.device.async_request_preview()


@_command("commit")
async def _commit(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    await light.device.async_commit_now()
    await light.coordinator.async_request_refresh()


@_command("find", {vol.Required("leds"): [int]})
async def _find(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    await light.async_find_leds(_text(msg["leds"]))


@_command("effect/render", _SPEC)
async def _effect_render(light: TwinklyCanvasLight, msg: dict[str, Any]) -> dict[str, Any]:
    """Frames for the card's preview; nothing goes to the lights."""
    spec = light.effect_spec(**_effect_args(msg["spec"]))
    fps = light.device.fps
    frames = await light.hass.async_add_executor_job(_preview, spec, light.store.canvas, fps)
    return {"fps": fps, "frames": frames}


@_command("effect/play", _SPEC)
async def _effect_play(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    await light.async_play_effect(**_effect_args(msg["spec"]))


@_command("builtin", {vol.Required("effect_id"): int})
async def _builtin(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    """Start a built-in effect by the device's effect_id, from 0."""
    await light.async_turn_on(effect=f"{PREFIX_BUILTIN}{msg['effect_id'] + 1}")


@_command("app_movie", {vol.Required("movie_id"): int})
async def _app_movie(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    await light.device.async_select_app_movie(msg["movie_id"])
    await light.coordinator.async_request_refresh()


@_command("group/save", {**_NAME, vol.Required("leds"): str})
async def _group_save(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    """Save a group; leds is selection text, the card sends format_selection of its picks."""
    await light.async_save_group(msg["name"], msg["leds"])


@_command("group/delete", _NAME)
async def _group_delete(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    await light.async_delete_group(msg["name"])


@_command("look/save", _NAME)
async def _look_save(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    await light.async_save_look(msg["name"])


@_command("look/apply", _NAME)
async def _look_apply(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    await light.async_apply_look(msg["name"])


@_command("look/update", _NAME)
async def _look_update(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    """Replace a saved look with what is on now."""
    _require_look(light, msg["name"])
    await light.async_save_look(msg["name"])


@_command("look/rename", {**_NAME, vol.Required("new_name"): str})
async def _look_rename(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    _require_look(light, msg["name"])
    light.store.rename_look(msg["name"], msg["new_name"])
    light.async_write_ha_state()  # the effect list shows the new name


@_command("look/delete", _NAME)
async def _look_delete(light: TwinklyCanvasLight, msg: dict[str, Any]) -> None:
    light.store.delete_look(msg["name"])
    light.async_write_ha_state()


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register the card's commands."""
    for handler in (
        _state,
        _paint,
        _commit,
        _find,
        _effect_render,
        _effect_play,
        _builtin,
        _app_movie,
        _group_save,
        _group_delete,
        _look_save,
        _look_apply,
        _look_update,
        _look_rename,
        _look_delete,
    ):
        websocket_api.async_register_command(hass, handler)
