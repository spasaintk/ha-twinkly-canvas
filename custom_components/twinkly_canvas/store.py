"""Persistent per-device state: canvas, groups, looks and our own movies."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .canvas import Canvas
from .const import DOMAIN
from .effects import EffectSpec
from .movies import movie_name
from .selection import (  # GROUP_NAME_MAX: re-export
    GROUP_NAME_MAX as GROUP_NAME_MAX,
    parse_selection,
    validate_group_name,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 1.0
LOOK_NAME_MAX = 40
ACTIVE_TYPES = ("painted", "custom_effect", "look", "builtin", "app_movie", "off")


@dataclass(frozen=True)
class Look:
    """A saved canvas, with a moving effect on top for moving looks."""

    name: str
    canvas: Canvas
    effect: EffectSpec | None

    @property
    def kind(self) -> str:
        return "static" if self.effect is None else "moving"


@dataclass(frozen=True)
class Active:
    """What the lights should show; ref names the look, effect or movie."""

    type: str
    ref: str | None = None

    def __post_init__(self) -> None:
        if self.type not in ACTIVE_TYPES:
            raise ValueError(f"Unknown active type: {self.type!r}")


def _effect(data: dict | None) -> EffectSpec | None:
    return EffectSpec.from_dict(data) if data else None


def _look_name(name: str) -> str:
    name = name.strip()
    if not 1 <= len(name) <= LOOK_NAME_MAX:
        raise ValueError(f"A look name needs 1 to {LOOK_NAME_MAX} characters")
    return name


def _fitted(led_count: int, data: str) -> tuple[Canvas, bool]:
    """A stored canvas cut, or padded with off, to led_count; True when its size differed.

    Products sold with extension kits can report another LED count after an extension.
    """
    raw = base64.b64decode(data)
    size = led_count * 4
    fit = raw[:size].ljust(size, b"\0")  # zero bytes are off
    canvas = Canvas(
        led_count,
        [tuple(fit[i : i + 4]) for i in range(0, size, 4)],  # type: ignore[misc]
    )
    return canvas, len(raw) != size


class CanvasStore:
    """State of one device; every change is saved 1 s later.

    Movie unique_ids are kept lower-cased like DeviceMovie.unique_id (the device
    lists them upper-cased).
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, led_count: int) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.led_count = led_count
        self._canvas = Canvas(led_count)
        self.groups: dict[str, str] = {}
        self.looks: dict[str, Look] = {}
        self._custom_effect: EffectSpec | None = None
        self.our_movies: list[str] = []
        self._current_movie_uid: str | None = None
        self._active = Active("painted", None)
        self._movie_counter = 0

    async def async_load(self) -> None:
        if (data := await self._store.async_load()) is None:
            return
        self._canvas, fitted = _fitted(self.led_count, data["canvas"])
        self.looks = {}
        for name, look in data["looks"].items():
            canvas, look_fitted = _fitted(self.led_count, look["canvas"])
            fitted |= look_fitted
            self.looks[name] = Look(name, canvas, _effect(look["effect"]))
        if fitted:
            _LOGGER.warning(
                "Stored canvases were made for another LED count: cut or padded with off to %s LEDs",
                self.led_count,
            )
        self.groups = data["groups"]
        self._custom_effect = _effect(data["custom_effect"])
        self.our_movies = data["our_movies"]
        self._current_movie_uid = data["current_movie_uid"]
        self._active = Active(**data["active"])
        self._movie_counter = data["movie_counter"]

    @property
    def canvas(self) -> Canvas:
        return self._canvas

    def set_canvas(self, canvas: Canvas) -> None:
        self._canvas = canvas.copy()
        self._save()

    def save_group(self, name: str, spec: str) -> None:
        """Save or replace a group; names are unique ignoring case, the new spelling wins."""
        name = validate_group_name(name)
        parse_selection(spec, self.led_count)
        self._pop_group(name)
        self.groups[name] = spec
        self._save()

    def delete_group(self, name: str) -> None:
        self._pop_group(name)
        self._save()

    def _pop_group(self, name: str) -> None:
        key = " ".join(name.split()).lower()
        for existing in [k for k in self.groups if k.lower() == key]:
            del self.groups[existing]

    def save_look(self, look: Look) -> None:
        name = _look_name(look.name)
        self.looks[name] = Look(name, look.canvas.copy(), look.effect)
        self._save()

    def rename_look(self, old: str, new: str) -> None:
        look = self.looks[old]
        new = _look_name(new)
        if new == old:
            return
        if new in self.looks:
            raise ValueError(f"A look named {new!r} already exists")
        del self.looks[old]
        self.looks[new] = Look(new, look.canvas, look.effect)
        if self._active == Active("look", old):
            self._active = Active("look", new)
        self._save()

    def delete_look(self, name: str) -> None:
        self.looks.pop(name, None)
        if self._active == Active("look", name):
            self._active = Active("painted", None)
        self._save()

    @property
    def custom_effect(self) -> EffectSpec | None:
        return self._custom_effect

    def set_custom_effect(self, spec: EffectSpec | None) -> None:
        self._custom_effect = spec
        self._save()

    def add_our_movie(self, uid: str) -> None:
        if uid.lower() not in self.our_movies:
            self.our_movies.append(uid.lower())
        self._save()

    def remove_our_movie(self, uid: str) -> None:
        if uid.lower() in self.our_movies:
            self.our_movies.remove(uid.lower())
        self._save()

    @property
    def current_movie_uid(self) -> str | None:
        return self._current_movie_uid

    @current_movie_uid.setter
    def current_movie_uid(self, uid: str | None) -> None:
        self._current_movie_uid = uid.lower() if uid else None
        self._save()

    @property
    def active(self) -> Active:
        return self._active

    @active.setter
    def active(self, active: Active) -> None:
        self._active = active
        self._save()

    def next_movie_name(self) -> str:
        self._movie_counter += 1
        self._save()
        return movie_name(self._movie_counter)

    async def async_remove(self) -> None:
        await self._store.async_remove()

    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        return {
            "canvas": self._canvas.to_b64(),
            "groups": self.groups,
            "looks": {
                name: {
                    "canvas": look.canvas.to_b64(),
                    "effect": look.effect.to_dict() if look.effect else None,
                }
                for name, look in self.looks.items()
            },
            "custom_effect": (
                self._custom_effect.to_dict() if self._custom_effect else None
            ),
            "our_movies": self.our_movies,
            "current_movie_uid": self._current_movie_uid,
            "active": asdict(self._active),
            "movie_counter": self._movie_counter,
        }
