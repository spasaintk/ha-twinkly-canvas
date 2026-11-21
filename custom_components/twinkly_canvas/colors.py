"""Colour parsing and wire packing. Colours are (r, g, b, w) tuples."""

from __future__ import annotations

from collections.abc import Sequence
import colorsys
import re

RGBW = tuple[int, int, int, int]


class ColorError(ValueError):
    """A colour value could not be understood."""


NAMED_COLORS: dict[str, RGBW] = {
    "off": (0, 0, 0, 0),
    "red": (255, 0, 0, 0),
    "green": (0, 200, 40, 0),
    "blue": (0, 60, 255, 0),
    "gold": (255, 215, 0, 0),
    "silver": (192, 192, 192, 0),
    "white": (255, 255, 255, 0),
    "warm white": (0, 0, 0, 255),
    "orange": (255, 80, 0, 0),
    "yellow": (255, 200, 0, 0),
    "purple": (150, 0, 255, 0),
    "pink": (255, 40, 120, 0),
    "cyan": (0, 200, 255, 0),
}

WARM_WHITE_RGB = (255, 180, 107)  # what one full W channel adds on an RGB device


def _clamp(x: float) -> int:
    return max(0, min(255, round(x)))


def parse_color(value: str | Sequence[int]) -> RGBW:
    """Parse a name, #RRGGBB, #RRGGBBWW or a sequence of 3 or 4 ints 0-255."""
    if isinstance(value, str):
        text = value.strip().lower()
        if not text.startswith("#"):
            name = text.replace("_", " ")
            if name not in NAMED_COLORS:
                raise ColorError(f"Unknown colour: {value!r}")
            return NAMED_COLORS[name]
        digits = text[1:]
        if not re.fullmatch(r"[0-9a-f]{6}(?:[0-9a-f]{2})?", digits):
            raise ColorError(f"Invalid hex colour: {value!r}")
        parts = [int(digits[i : i + 2], 16) for i in range(0, len(digits), 2)]
    elif isinstance(value, Sequence):
        parts = list(value)
    else:
        raise ColorError(f"Invalid colour: {value!r}")
    if len(parts) not in (3, 4) or not all(
        isinstance(p, int) and not isinstance(p, bool) and 0 <= p <= 255 for p in parts
    ):
        raise ColorError(f"Invalid colour sequence: {value!r}")
    return (*parts, *([0] * (4 - len(parts))))  # type: ignore[return-value]


def to_wire(color: RGBW, profile: str) -> bytes:
    """Pack one LED: W,R,G,B for RGBW devices; R,G,B with W added as warm white for RGB."""
    r, g, b, w = color
    if profile == "RGBW":
        return bytes([w, r, g, b])
    if profile == "RGB":
        return bytes(_clamp(c + w * k / 255) for c, k in zip((r, g, b), WARM_WHITE_RGB))
    raise ValueError(f"Unsupported LED profile: {profile!r}")


def blend(a: RGBW, b: RGBW, t: float) -> RGBW:
    """Linear blend from a (t=0) to b (t=1)."""
    return tuple(_clamp(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def scale(color: RGBW, factor: float) -> RGBW:
    """Multiply every channel by factor."""
    return tuple(_clamp(c * factor) for c in color)  # type: ignore[return-value]


def hsv_to_rgbw(h: float, s: float, v: float) -> RGBW:
    """HSV (all 0..1) to RGBW with W = 0."""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (_clamp(r * 255), _clamp(g * 255), _clamp(b * 255), 0)
