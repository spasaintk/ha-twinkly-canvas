"""Persistent per-device state: canvas, groups, looks and our own movies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .canvas import Canvas
from .const import DOMAIN
from .effects import EffectSpec
from .movies import movie_name
from .selection import parse_selection

STORAGE_VERSION = 1
SAVE_DELAY = 1.0
GROUP_NAME_MAX = 40
ACTIVE_TYPES = ("painted", "custom_effect", "look", "builtin", "app_movie", "off")


@dataclass
class Look:
    """A saved canvas, with a moving effect on top for moving looks."""

    name: str
    canvas: Canvas
    effect: EffectSpec | None

    @property
    def kind(self) -> str:
        return "static" if self.effect is None else "moving"


@dataclass
class Active:
    """What the lights should show; ref names the look, effect or movie."""

    type: str
    ref: str | None = None

    def __post_init__(self) -> None:
        if self.type not in ACTIVE_TYPES:
            raise ValueError(f"Unknown active type: {self.type!r}")


def _effect(data: dict | None) -> EffectSpec | None:
    return EffectSpec.from_dict(data) if data else None


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
        self.custom_effect: EffectSpec | None = None
        self.our_movies: list[str] = []
        self._current_movie_uid: str | None = None
        self._active = Active("painted", None)
        self._movie_counter = 0

    async def async_load(self) -> None:
        if (data := await self._store.async_load()) is None:
            return
        # shortcut: stored canvases must match led_count (from_b64 raises), fit them
        # to the new count if a device's LED count can ever change
        self._canvas = Canvas.from_b64(self.led_count, data["canvas"])
        self.groups = data["groups"]
        self.looks = {
            name: Look(
                name,
                Canvas.from_b64(self.led_count, look["canvas"]),
                _effect(look["effect"]),
            )
            for name, look in data["looks"].items()
        }
        self.custom_effect = _effect(data["custom_effect"])
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
        name = name.strip()
        if not 1 <= len(name) <= GROUP_NAME_MAX:
            raise ValueError(f"A group name needs 1 to {GROUP_NAME_MAX} characters")
        parse_selection(spec, self.led_count)
        self.groups[name] = spec
        self._save()

    def delete_group(self, name: str) -> None:
        self.groups.pop(name.strip(), None)
        self._save()

    def save_look(self, look: Look) -> None:
        self.looks[look.name] = Look(look.name, look.canvas.copy(), look.effect)
        self._save()

    def rename_look(self, old: str, new: str) -> None:
        if new in self.looks:
            raise ValueError(f"A look named {new!r} already exists")
        look = self.looks.pop(old)
        self.looks[new] = Look(new, look.canvas, look.effect)
        if self._active == Active("look", old):
            self._active = Active("look", new)
        self._save()

    def delete_look(self, name: str) -> None:
        self.looks.pop(name, None)
        self._save()

    def set_custom_effect(self, spec: EffectSpec | None) -> None:
        self.custom_effect = spec
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
                self.custom_effect.to_dict() if self.custom_effect else None
            ),
            "our_movies": self.our_movies,
            "current_movie_uid": self._current_movie_uid,
            "active": asdict(self._active),
            "movie_counter": self._movie_counter,
        }
