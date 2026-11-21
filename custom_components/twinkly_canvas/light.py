"""The device as one Home Assistant light; its effects are looks, built-ins and app movies."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .canvas import Canvas
from .colors import parse_color, to_wire
from .const import (
    DOMAIN,
    EFFECT_CUSTOM,
    EFFECT_PAINTED,
    PREFIX_APP,
    PREFIX_BUILTIN,
    PREFIX_LOOK,
)
from .coordinator import TwinklyCanvasConfigEntry, TwinklyCanvasData
from .device import DeviceState
from .effects import EffectSpec
from .entity import TwinklyCanvasEntity
from .movies import DeviceMovie
from .selection import parse_selection, validate_group_name
from .store import Active, CanvasStore, Look

PARALLEL_UPDATES = 1


@contextmanager
def _invalid(translation_key: str) -> Iterator[None]:
    """Raise a ValueError (bad selection, colour or name) as a translated validation error."""
    try:
        yield
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={"error": str(err)},
        ) from err


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TwinklyCanvasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TwinklyCanvasLight(entry.runtime_data)])


def _app_movies(store: CanvasStore, state: DeviceState) -> dict[str, DeviceMovie]:
    """The movies that are not ours, by effect name; a repeated name gets " (2)", " (3)"."""
    named: dict[str, DeviceMovie] = {}
    for movie in state.movies.movies:
        if movie.unique_id in store.our_movies:
            continue
        name, n = f"{PREFIX_APP}{movie.name}", 1
        while name in named:
            n += 1
            name = f"{PREFIX_APP}{movie.name} ({n})"
        named[name] = movie
    return named


def effect_options(store: CanvasStore, state: DeviceState) -> list[str]:
    """The canvas, the custom effect, the looks, the built-in effects, the app's movies."""
    return [
        EFFECT_PAINTED,
        *([EFFECT_CUSTOM] if store.custom_effect else []),
        *(f"{PREFIX_LOOK}{name}" for name in sorted(store.looks, key=str.casefold)),
        *(f"{PREFIX_BUILTIN}{n}" for n in range(1, state.effects_count + 1)),
        *_app_movies(store, state),
    ]


class TwinklyCanvasLight(TwinklyCanvasEntity, LightEntity):
    """All LEDs as one light: colour fills the canvas, effects pick what plays."""

    _attr_name = None
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, data: TwinklyCanvasData) -> None:
        super().__init__(data)
        self._attr_unique_id = f"{data.info.mac}_light"
        self._color_mode = (
            ColorMode.RGB if data.info.profile == "RGB" else ColorMode.RGBW
        )
        self._attr_supported_color_modes = {self._color_mode}

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.mode != "off"

    @property
    def brightness(self) -> int:
        return round(self.coordinator.data.brightness * 255 / 100)

    @property
    def effect_list(self) -> list[str]:
        return effect_options(self.store, self.coordinator.data)

    @property
    def effect(self) -> str | None:
        """The entry for what plays: the device mode first, then store.active."""
        state = self.coordinator.data
        if state.mode == "effect":
            return f"{PREFIX_BUILTIN}{state.effect_id + 1}"
        if state.mode != "movie" or (movie := state.current_movie) is None:
            return None
        if movie.unique_id not in self.store.our_movies:
            apps = _app_movies(self.store, state)
            return next((name for name, m in apps.items() if m == movie), None)
        active = self.store.active
        return {
            "painted": EFFECT_PAINTED,
            "custom_effect": EFFECT_CUSTOM,
            "look": f"{PREFIX_LOOK}{active.ref}",
        }.get(active.type)

    @property
    def color_mode(self) -> ColorMode:
        # HA allows BRIGHTNESS only while an effect runs; a painted canvas takes colours
        if self.effect in (None, EFFECT_PAINTED):
            return self._color_mode
        return ColorMode.BRIGHTNESS

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        if self.store.active.type == "painted":
            return self.store.canvas.uniform_color()
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """For RGB lights: the colour they show, W folded in as warm white."""
        if (color := self.rgbw_color) is None:
            return None
        return tuple(to_wire(color, "RGB"))  # type: ignore[return-value]

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT] not in self.effect_list:
            raise ServiceValidationError(  # before anything changes, brightness too
                translation_domain=DOMAIN,
                translation_key="unknown_effect",
                translation_placeholders={"effect": kwargs[ATTR_EFFECT]},
            )
        if (
            ATTR_BRIGHTNESS in kwargs
        ):  # HA's light service turns brightness 0 into turn_off
            percent = max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
            await self.device.async_set_brightness(percent)
        if ATTR_RGBW_COLOR in kwargs or ATTR_RGB_COLOR in kwargs:
            canvas = Canvas(self.store.led_count)
            canvas.fill(kwargs.get(ATTR_RGBW_COLOR) or (*kwargs[ATTR_RGB_COLOR], 0))
            await self.device.async_show_canvas(canvas, Active("painted", None))
        elif ATTR_EFFECT in kwargs:
            await self._async_select_effect(kwargs[ATTR_EFFECT])
        elif ATTR_BRIGHTNESS not in kwargs or not self.is_on:
            await self.device.async_turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_turn_off()
        await self.coordinator.async_request_refresh()

    async def _async_select_effect(self, effect: str) -> None:
        """Show an effect list entry (async_turn_on checked it); its prefix says what it is."""
        store, state = self.store, self.coordinator.data
        if effect == EFFECT_PAINTED:
            await self.device.async_show_canvas(store.canvas, Active("painted", None))
        elif effect == EFFECT_CUSTOM:
            await self.device.async_play_effect(
                store.custom_effect, store.canvas, Active("custom_effect", None)
            )
        elif effect.startswith(PREFIX_LOOK):
            await self.device.async_apply_look(effect.removeprefix(PREFIX_LOOK))
        elif effect.startswith(PREFIX_BUILTIN):
            await self.device.async_select_builtin(
                int(effect.removeprefix(PREFIX_BUILTIN)) - 1
            )
        else:
            await self.device.async_select_app_movie(
                _app_movies(store, state)[effect].id
            )

    # Actions (services.py); LED numbers in selections count from 1.

    async def async_set_leds(
        self,
        leds: str,
        clear_others: bool,
        color: Any = None,
        color_name: str | None = None,
        white: int = 0,
    ) -> None:
        """Paint the selected LEDs one colour and show the canvas; white adds to W."""
        if (color is None) == (color_name is None):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_color",
                translation_placeholders={
                    "error": "Give exactly one of a colour and a colour name"
                },
            )
        with _invalid("invalid_color"):
            r, g, b, w = parse_color(color_name if color is None else color)
        indices = self._selection(leds)
        canvas = (
            Canvas(self.store.led_count) if clear_others else self.store.canvas.copy()
        )
        canvas.set(indices, (r, g, b, min(255, w + white)))
        await self.device.async_show_canvas(canvas, Active("painted", None))
        await self.coordinator.async_request_refresh()

    async def async_play_effect(
        self, kind: str, colors: list, leds: str, speed: float, keep_others: bool
    ) -> None:
        """Play a moving effect (speed 0-100) and keep it as the custom effect."""
        spec = self.effect_spec(kind, colors, leds, speed, keep_others)
        await self.device.async_play_effect(
            spec, self.store.canvas, Active("custom_effect", None)
        )
        self.store.set_custom_effect(
            spec
        )  # once it plays: a failed upload keeps the old one
        await self.coordinator.async_request_refresh()

    def effect_spec(
        self, kind: str, colors: list, leds: str, speed: float, keep_others: bool
    ) -> EffectSpec:
        """The checked effect for play_effect's arguments; the card's preview uses it too."""
        with _invalid("invalid_color"):
            parsed = tuple(parse_color(c) for c in colors)
        return EffectSpec(
            kind, tuple(self._selection(leds)), parsed, speed / 100, keep_others
        )

    async def async_apply_look(self, look: str) -> None:
        await self.device.async_apply_look(look)
        await self.coordinator.async_request_refresh()

    async def async_save_look(self, name: str) -> None:
        """Save what is on now: the painting, the custom effect over it, or a copy of a look."""
        store, active = self.store, self.store.active
        if active.type == "painted":
            look = Look(name, store.canvas, None)
        elif active.type == "custom_effect":
            look = Look(name, store.canvas, store.custom_effect)
        elif active.type == "look" and (current := store.find_look(active.ref or "")):
            look = replace(current, name=name)
        else:  # a built-in effect or an app movie: there are no frames of ours to keep
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="cannot_save_look"
            )
        with _invalid("invalid_name"):
            store.save_look(look)
        self.async_write_ha_state()  # the effect list shows the new look

    async def async_find_leds(self, leds: str) -> None:
        await self.device.async_find(self._selection(leds))

    async def async_save_group(self, name: str, leds: str) -> None:
        with _invalid("invalid_name"):
            validate_group_name(name)
        with _invalid("invalid_selection"):
            self.store.save_group(name, leds)

    async def async_delete_group(self, name: str) -> None:
        try:
            self.store.delete_group(name)
        except KeyError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_group",
                translation_placeholders={"name": name},
            ) from err

    def _selection(self, leds: str) -> list[int]:
        """The 0-based indices of a selection text, which may name groups."""
        with _invalid("invalid_selection"):
            return parse_selection(leds, self.store.led_count, self.store.groups)
