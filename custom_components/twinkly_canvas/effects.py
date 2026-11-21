"""Moving effects, rendered in pure Python as seamless frame loops.

Every effect is a function of the loop phase (t mod L) / L, so frame L equals
frame 0. Speed sets the natural loop length; a loop longer than the frame
budget moves faster to fit instead of being cut short.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
import math
import random

from .canvas import OFF, Canvas
from .colors import NAMED_COLORS, RGBW, hsv_to_rgbw, parse_color, scale, to_wire

EFFECT_KINDS = (
    "twinkle",
    "chase",
    "odd_even_swap",
    "wave",
    "comet",
    "candy_cane",
    "breathe",
    "rainbow",
)

DEFAULT_COLORS: dict[str, tuple[RGBW, ...]] = {
    "twinkle": (NAMED_COLORS["warm white"],),
    "chase": (NAMED_COLORS["red"], NAMED_COLORS["silver"]),
    "odd_even_swap": (NAMED_COLORS["red"], NAMED_COLORS["silver"]),
    "wave": (NAMED_COLORS["red"], NAMED_COLORS["gold"], NAMED_COLORS["green"]),
    "comet": (NAMED_COLORS["warm white"],),
    "candy_cane": (NAMED_COLORS["red"], NAMED_COLORS["white"]),
    "breathe": (NAMED_COLORS["red"],),
    "rainbow": (),
}

# Moving patterns: (slow, fast) in LEDs per second. One repeat passes per loop.
_LEDS_PER_SECOND: dict[str, tuple[float, float]] = {
    "chase": (2.0, 15.0),
    "comet": (4.0, 40.0),
    "candy_cane": (1.0, 12.0),
    "wave": (2.0, 25.0),
    "rainbow": (100 / 30, 100 / 3),  # one colour wheel in 30 s to 3 s
}

_CROSSFADE_SECONDS = 0.3  # odd_even_swap
_GAMMA = 2.2
_LEVEL = [(v / 255) ** (1 / _GAMMA) for v in range(256)]  # drive -> perceived, 0..1


@dataclass(frozen=True)
class EffectSpec:
    """A moving effect on LEDs (0-based); empty colors means DEFAULT_COLORS[kind]."""

    kind: str
    leds: tuple[int, ...]
    colors: tuple[RGBW, ...] = ()
    speed: float = 0.5
    keep_others: bool = True
    seed: int = 1

    def __post_init__(self) -> None:
        # Lists and tuples must compare and hash alike; the dataclass is frozen.
        object.__setattr__(self, "leds", tuple(self.leds))
        object.__setattr__(self, "colors", tuple(parse_color(c) for c in self.colors))
        if self.kind not in EFFECT_KINDS:
            raise ValueError(f"Unknown effect: {self.kind!r}")
        if not 0 <= self.speed <= 1:
            raise ValueError(f"Speed must be between 0 and 1, got {self.speed!r}")
        if not self.leds:
            raise ValueError("An effect needs at least one LED")
        if len(self.colors) > 3:
            raise ValueError("An effect takes at most 3 colours")

    def to_dict(self) -> dict:
        """JSON-safe form."""
        return {
            **asdict(self),
            "leds": list(self.leds),
            "colors": [list(c) for c in self.colors],
        }

    @classmethod
    def from_dict(cls, data: dict) -> EffectSpec:
        """Inverse of to_dict."""
        return cls(**data)


def loop_length(spec: EffectSpec, fps: int, max_frames: int = 240) -> int:
    """Frames in one seamless loop: the speed's natural length, at most max_frames."""
    colors = spec.colors or DEFAULT_COLORS.get(spec.kind, ())
    if spec.kind in _LEDS_PER_SECOND:
        cells = _cells(spec.kind, colors)
        seconds = len(cells) / _by_speed(spec, *_LEDS_PER_SECOND[spec.kind])
    elif spec.kind == "breathe":  # one fade per stop; one colour has two (dim, full)
        seconds = max(2, len(colors)) * _by_speed(spec, 4.0, 0.5)
    elif spec.kind == "odd_even_swap":  # hold, crossfade, hold swapped, fade back
        seconds = 2 * (_by_speed(spec, 5.0, 0.5) + _CROSSFADE_SECONDS)
    elif spec.kind == "twinkle":  # a fixed loop; speed sets how often each LED twinkles
        seconds = 12.0
    else:
        raise ValueError(f"Unknown effect: {spec.kind!r}")
    return max(1, min(max_frames, round(seconds * fps)))


def render_frame(
    spec: EffectSpec,
    base: Canvas,
    t: int,
    fps: int,
    profile: str = "RGBW",
    max_frames: int = 240,
) -> bytes:
    """Frame t of the loop as device bytes; LEDs outside the effect show base or off."""
    leds = sorted(set(spec.leds))
    if leds[0] < 0 or leds[-1] >= base.led_count:
        raise ValueError(f"Effect LEDs must be within 0..{base.led_count - 1}")
    length = loop_length(spec, fps, max_frames)
    pixels = _effect_colors(spec, base, leds, (t % length) / length, length / fps)
    size = len(to_wire(OFF, profile))
    if spec.keep_others:
        frame = bytearray(base.to_frame(profile))
    else:
        frame = bytearray(size * base.led_count)
    for led, color in zip(leds, pixels):
        frame[led * size : led * size + size] = to_wire(color, profile)
    return bytes(frame)


def render(
    spec: EffectSpec,
    base: Canvas,
    fps: int,
    profile: str = "RGBW",
    max_frames: int = 240,
) -> list[bytes]:
    """Every frame of one seamless loop."""
    return [
        render_frame(spec, base, t, fps, profile, max_frames)
        for t in range(loop_length(spec, fps, max_frames))
    ]


def _effect_colors(
    spec: EffectSpec, base: Canvas, leds: list[int], u: float, seconds: float
) -> list[RGBW]:
    """Colours of the effect LEDs at phase u (0..1) of a loop lasting seconds."""
    colors = spec.colors or DEFAULT_COLORS.get(spec.kind, ())
    if spec.kind in _LEDS_PER_SECOND:
        cells = _cells(spec.kind, colors)
        shift = u * len(cells)
        return [_sample(cells, i - shift) for i in range(len(leds))]
    if spec.kind == "breathe":  # all LEDs together
        stops = colors if len(colors) > 1 else (scale(colors[0], 0.15), colors[0])
        x = u * len(stops)
        k = math.floor(x)
        return [_sample(stops, k + _ease(x - k))] * len(leds)
    if spec.kind == "odd_even_swap":
        a, b = _two(colors)[:2]
        fade = min(0.5, _CROSSFADE_SECONDS / seconds)  # the crossfade as a share of the loop
        swap = _ease((min(u, 1 - u) - 0.25) / fade + 0.5)  # 0 at the start, 1 halfway
        # Odd and even are LED numbers counted from 1: index 0 is LED 1 (odd).
        return [
            _fade(a, b, swap) if led % 2 == 0 else _fade(b, a, swap) for led in leds
        ]
    if spec.kind != "twinkle":
        raise ValueError(f"Unknown effect: {spec.kind!r}")
    # twinkle: each LED fades from its base colour to a twinkle colour and back
    rng = random.Random(spec.seed)
    per_loop = _by_speed(spec, 0.1, 1.0) * seconds  # twinkles per LED per loop
    out: list[RGBW] = []
    for led in leds:
        phase = rng.random()
        # a whole number of twinkles per loop keeps the loop seamless
        cycles = max(1, round(per_loop * (0.5 + rng.random())))
        level = ((1 - math.cos(2 * math.pi * (cycles * u + phase))) / 2) ** 3
        out.append(_fade(base.pixels[led], rng.choice(colors), level))
    return out


def _cells(kind: str, colors: tuple[RGBW, ...]) -> list[RGBW]:
    """One repeat of a moving pattern, one colour per LED."""
    if kind == "chase":  # a band of 3 per colour, each followed by a gap of 3
        return [c for color in colors for c in [color] * 3 + [OFF] * 3]
    if kind == "candy_cane":  # stripes 4 wide
        return [c for color in _two(colors) for c in [color] * 4]
    if kind == "comet":  # one per 40 LEDs per colour: dark, a tail of 8, the head
        return [
            c
            for color in colors
            for c in [OFF] * 31
            + [_fade(OFF, color, 1 - d / 9) for d in range(8, 0, -1)]
            + [color]
        ]
    if kind == "wave":  # a smooth cycle through the colours over 50 LEDs
        stops = _two(colors)
        return [_sample(stops, j * len(stops) / 50) for j in range(50)]
    if kind == "rainbow":
        return [hsv_to_rgbw(j / 100, 1, 1) for j in range(100)]
    raise ValueError(f"Unknown moving pattern: {kind!r}")


def _two(colors: tuple[RGBW, ...]) -> tuple[RGBW, ...]:
    """At least two colours: a single colour alternates with off."""
    return colors if len(colors) > 1 else (colors[0], OFF)


def _by_speed(spec: EffectSpec, slow: float, fast: float) -> float:
    """slow at speed 0, fast at speed 1, geometric in between."""
    return slow * (fast / slow) ** spec.speed


def _ease(x: float) -> float:
    """Smooth ramp from 0 to 1 as x goes 0..1 (clamped), flat at both ends."""
    return (1 - math.cos(math.pi * min(1.0, max(0.0, x)))) / 2


def _sample(seq: Sequence[RGBW], x: float) -> RGBW:
    """Colour at position x of seq repeated forever, fading between neighbours."""
    k = math.floor(x)
    return _fade(seq[k % len(seq)], seq[(k + 1) % len(seq)], x - k)


def _fade(a: RGBW, b: RGBW, t: float) -> RGBW:
    """Gamma-correct fade (gamma 2.2) from a (t=0) to b (t=1): even perceived steps."""
    return tuple(  # type: ignore[return-value]
        round(255 * (_LEVEL[x] + (_LEVEL[y] - _LEVEL[x]) * t) ** _GAMMA)
        for x, y in zip(a, b)
    )
