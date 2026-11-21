"""Per-LED colour canvas."""

from __future__ import annotations

import base64
from collections.abc import Iterable

from .colors import RGBW, to_wire

OFF: RGBW = (0, 0, 0, 0)


class Canvas:
    """One RGBW colour per LED (0-based)."""

    def __init__(self, led_count: int, pixels: list[RGBW] | None = None) -> None:
        self.led_count = led_count
        self.pixels: list[RGBW] = list(pixels) if pixels is not None else [OFF] * led_count

    def set(self, indices: Iterable[int], color: RGBW) -> None:
        """Paint the given LEDs; validates every index before changing any."""
        indices = list(indices)
        for i in indices:
            if not 0 <= i < self.led_count:
                raise ValueError(f"LED index {i} outside 0..{self.led_count - 1}")
        for i in indices:
            self.pixels[i] = color

    def fill(self, color: RGBW) -> None:
        self.pixels = [color] * self.led_count

    def copy(self) -> Canvas:
        return Canvas(self.led_count, self.pixels)

    def to_frame(self, profile: str) -> bytes:
        return b"".join(to_wire(p, profile) for p in self.pixels)

    def to_b64(self) -> str:
        """Storage format: R,G,B,W bytes per LED, base64."""
        return base64.b64encode(bytes(v for p in self.pixels for v in p)).decode()

    @classmethod
    def from_b64(cls, led_count: int, data: str) -> Canvas:
        raw = base64.b64decode(data)
        if len(raw) != led_count * 4:
            raise ValueError("Stored canvas does not match the LED count")
        return cls(
            led_count,
            [tuple(raw[i : i + 4]) for i in range(0, len(raw), 4)],  # type: ignore[misc]
        )

    def uniform_color(self) -> RGBW | None:
        first = self.pixels[0] if self.pixels else None
        return first if first is not None and all(p == first for p in self.pixels) else None
