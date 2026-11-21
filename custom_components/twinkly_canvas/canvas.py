"""Per-LED colour canvas."""

from __future__ import annotations

import base64
from collections.abc import Iterable

from .colors import RGBW, parse_color, to_wire

OFF: RGBW = (0, 0, 0, 0)


def _checked(color: RGBW) -> RGBW:
    """The colour as a tuple; ValueError unless it is 4 ints 0-255 (r, g, b, w)."""
    if not isinstance(color, (tuple, list)) or len(color) != 4:
        raise ValueError(f"A canvas colour needs 4 values (r, g, b, w), got {color!r}")
    return parse_color(color)  # checks each value: an int 0-255, not a bool


class Canvas:
    """One RGBW colour per LED (0-based)."""

    def __init__(self, led_count: int, pixels: list[RGBW] | None = None) -> None:
        self.led_count = led_count
        self.pixels: list[RGBW] = (
            list(pixels) if pixels is not None else [OFF] * led_count
        )
        if len(self.pixels) != led_count:
            raise ValueError(f"{len(self.pixels)} pixels given for {led_count} LEDs")

    def set(self, indices: Iterable[int], color: RGBW) -> None:
        """Paint the given LEDs; validates the colour and every index before changing any."""
        color = _checked(color)
        indices = list(indices)
        for i in indices:
            if not 0 <= i < self.led_count:
                raise ValueError(f"LED index {i} outside 0..{self.led_count - 1}")
        for i in indices:
            self.pixels[i] = color

    def fill(self, color: RGBW) -> None:
        self.pixels = [_checked(color)] * self.led_count

    def copy(self) -> Canvas:
        return Canvas(self.led_count, self.pixels)

    def to_frame(self, profile: str) -> bytes:
        return b"".join(to_wire(p, profile) for p in self.pixels)

    def to_b64(self) -> str:
        """Storage format: R,G,B,W bytes per LED, base64."""
        return base64.b64encode(bytes(v for p in self.pixels for v in p)).decode()

    @classmethod
    def from_b64(cls, led_count: int, data: str) -> Canvas:
        canvas, resized = cls.from_b64_fitted(led_count, data)
        if resized:
            raise ValueError("Stored canvas does not match the LED count")
        return canvas

    @classmethod
    def from_b64_fitted(cls, led_count: int, data: str) -> tuple[Canvas, bool]:
        """A stored canvas cut, or padded with off, to led_count; True when its size differed."""
        raw = base64.b64decode(data)
        size = led_count * 4
        fit = raw[:size].ljust(size, b"\0")  # zero bytes are off
        pixels = [tuple(fit[i : i + 4]) for i in range(0, size, 4)]
        return cls(led_count, pixels), len(raw) != size  # type: ignore[arg-type]

    def uniform_color(self) -> RGBW | None:
        first = self.pixels[0] if self.pixels else None
        return (
            first
            if first is not None and all(p == first for p in self.pixels)
            else None
        )
