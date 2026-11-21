"""Tests for the effect renderer."""

import json

import pytest

from custom_components.twinkly_canvas.canvas import Canvas
from custom_components.twinkly_canvas.colors import NAMED_COLORS, ColorError, to_wire
from custom_components.twinkly_canvas.effects import (
    EFFECT_KINDS,
    EffectSpec,
    _cells,
    _effect_colors,
    loop_length,
    render,
    render_frame,
)

SILVER = NAMED_COLORS["silver"]
RED = NAMED_COLORS["red"]
GREEN = NAMED_COLORS["green"]
GOLD = NAMED_COLORS["gold"]
LEDS = tuple(range(37, 80))
ALL = tuple(range(400))
FPS = 10
DEVICE_FPS = 11  # what the lights play


@pytest.fixture
def base() -> Canvas:
    canvas = Canvas(400)
    canvas.fill(SILVER)
    return canvas


@pytest.fixture
def stripes() -> Canvas:
    """LEDs 1, 3, 5, ... silver and 2, 4, 6, ... red: where the strobe was seen."""
    return Canvas(400, [SILVER if i % 2 == 0 else RED for i in range(400)])


def _jump(a: bytes, b: bytes) -> int:
    """Largest change of any byte between two frames."""
    return max(abs(x - y) for x, y in zip(a, b))


def _assert_smooth_loop(frames: list[bytes]) -> None:
    """The loop moves, and wrapping round is no bigger a step than any other."""
    steps = [_jump(a, b) for a, b in zip(frames, frames[1:])]
    assert max(steps) > 0
    assert _jump(frames[-1], frames[0]) <= max(steps)


def _led(frame: bytes, index: int) -> bytes:
    return frame[index * 4 : index * 4 + 4]


@pytest.mark.parametrize("kind", EFFECT_KINDS)
def test_loop_is_seamless(kind: str, base: Canvas) -> None:
    spec = EffectSpec(kind, LEDS)
    length = loop_length(spec, FPS)
    assert 1 <= length <= 240
    assert render_frame(spec, base, length, FPS) == render_frame(spec, base, 0, FPS)
    frames = render(spec, base, FPS)
    assert len(frames) == length
    _assert_smooth_loop(frames)


@pytest.mark.parametrize("kind", EFFECT_KINDS)
def test_frame_size(kind: str, base: Canvas) -> None:
    spec = EffectSpec(kind, LEDS)
    assert len(render_frame(spec, base, 0, FPS)) == 1600
    assert len(render_frame(spec, base, 0, FPS, "RGB")) == 1200


@pytest.mark.parametrize("kind", EFFECT_KINDS)
def test_keep_others(kind: str, base: Canvas) -> None:
    for keep, expected in ((True, to_wire(SILVER, "RGBW")), (False, bytes(4))):
        frames = render(EffectSpec(kind, LEDS, keep_others=keep), base, FPS)
        assert all(_led(frame, 0) == expected for frame in frames)


@pytest.mark.parametrize("kind", EFFECT_KINDS)
def test_keep_others_leaves_other_leds_untouched(kind: str) -> None:
    base = Canvas(
        400, [(i % 256, i // 256, 255 - i % 256, i * 7 % 256) for i in range(400)]
    )
    spec = EffectSpec(kind, (0, *LEDS, 399))
    outside = [i for i in range(400) if i not in spec.leds]
    expected = [_led(base.to_frame("RGBW"), i) for i in outside]
    for frame in render(spec, base, FPS):
        assert [_led(frame, i) for i in outside] == expected


@pytest.mark.parametrize("kind", EFFECT_KINDS)
def test_budget_cap(kind: str, base: Canvas) -> None:
    for speed in (0, 0.5, 1):
        spec = EffectSpec(kind, LEDS, speed=speed)
        length = loop_length(spec, FPS, max_frames=60)
        assert length <= 60
        frames = render(spec, base, FPS, max_frames=60)
        assert len(frames) == length
        _assert_smooth_loop(frames)  # parameters changed to fit, not cut short


def test_odd_even_swap_alternates(base: Canvas) -> None:
    spec = EffectSpec("odd_even_swap", LEDS, colors=(RED, GREEN))
    red, green = to_wire(RED, "RGBW"), to_wire(GREEN, "RGBW")
    first = render_frame(spec, base, 0, FPS)
    assert (_led(first, 38), _led(first, 39)) == (red, green)  # LED numbers 39, 40
    middle = render_frame(spec, base, loop_length(spec, FPS) // 2, FPS)
    assert (_led(middle, 38), _led(middle, 39)) == (green, red)


def test_deterministic(base: Canvas) -> None:
    first = render(EffectSpec("twinkle", LEDS, seed=7), base, FPS)
    assert render(EffectSpec("twinkle", LEDS, seed=7), base, FPS) == first
    assert render(EffectSpec("twinkle", LEDS, seed=8), base, FPS) != first


@pytest.mark.parametrize("speed", [0, 0.5, 1])
@pytest.mark.parametrize(
    "kind", ["chase", "candy_cane", "comet", "wave", "rainbow", "odd_even_swap"]
)
def test_moving_light_output_is_steady(
    kind: str, speed: float, stripes: Canvas
) -> None:
    """The LEDs dim by pulsing, so light follows the byte value: a pattern moving
    between LEDs, or a swap, keeps the light of the whole tree steady (no strobe)."""
    frames = render(EffectSpec(kind, ALL, speed=speed), stripes, DEVICE_FPS)
    totals = [sum(frame) for frame in frames]
    mean = sum(totals) / len(totals)
    assert max(abs(total - mean) for total in totals) <= 0.03 * mean


@pytest.mark.parametrize("speed", [0, 0.5])
def test_odd_even_swap_fades(speed: float, stripes: Canvas) -> None:
    """The swap is a fade over 10 frames or more, in small steps, not a switch."""
    frames = render(EffectSpec("odd_even_swap", ALL, speed=speed), stripes, DEVICE_FPS)
    ends = {to_wire(RED, "RGBW"), to_wire(SILVER, "RGBW")}
    swap = frames[: len(frames) // 2]  # the second half swaps back
    assert sum(_led(frame, 0) not in ends for frame in swap) >= 10
    if speed == 0:
        assert max(_jump(a, b) for a, b in zip(frames, frames[1:] + frames[:1])) <= 40


def test_odd_even_swap_loop_lengths() -> None:
    """A longer fade fits in the same loop."""
    specs = [EffectSpec("odd_even_swap", ALL, speed=speed) for speed in (0, 0.5, 1)]
    assert [loop_length(spec, DEVICE_FPS) for spec in specs] == [117, 41, 18]


def test_twinkle_changes_every_led(stripes: Canvas) -> None:
    """A twinkle colour is never the LED's own colour while the palette has another."""
    spec = EffectSpec("twinkle", ALL, colors=(RED, SILVER, GOLD), speed=0)
    frames = render(spec, stripes, DEVICE_FPS)
    assert [i for i in ALL if len({_led(frame, i) for frame in frames}) == 1] == []


def test_twinkle_in_its_own_colour_dims(stripes: Canvas) -> None:
    """With only its own colour to twinkle to, an LED fades to about 30% and back."""
    frames = render(
        EffectSpec("twinkle", (0,), (SILVER,), speed=0), stripes, DEVICE_FPS
    )
    reds = [_led(frame, 0)[1] for frame in frames]  # W, R, G, B
    assert 192 in reds
    assert 0.25 * 192 <= min(reds) <= 0.35 * 192


def test_comet_tail_keeps_its_shape() -> None:
    """The tail dims in even steps to the eye (gamma 2.2), value for value as in 0.1.0."""
    for v in range(256):
        tail = _cells("comet", ((v, v, v, v),))[31:39]
        before = [
            round(255 * ((v / 255) ** (1 / 2.2) * (1 - d / 9)) ** 2.2)
            for d in range(8, 0, -1)
        ]
        assert tail == [(x, x, x, x) for x in before]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "sparkle", "leds": LEDS},
        {"kind": "chase", "leds": LEDS, "speed": 1.5},
        {"kind": "chase", "leds": ()},
        {"kind": "chase", "leds": LEDS, "colors": (RED, GREEN, SILVER, RED)},
    ],
)
def test_spec_validation(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        EffectSpec(**kwargs)


def test_spec_roundtrip() -> None:
    spec = EffectSpec(
        "wave",
        LEDS,
        colors=(RED, (0, 0, 0, 255)),
        speed=0.25,
        keep_others=False,
        seed=3,
    )
    assert EffectSpec.from_dict(spec.to_dict()) == spec
    assert EffectSpec.from_dict(json.loads(json.dumps(spec.to_dict()))) == spec


def test_spec_stores_tuples_and_parsed_colors() -> None:
    listed = EffectSpec("chase", [1, 2], [[255, 0, 0]])
    tupled = EffectSpec("chase", (1, 2), ((255, 0, 0, 0),))
    assert listed == tupled
    assert hash(listed) == hash(tupled)


def test_spec_rejects_bad_color() -> None:
    with pytest.raises(ColorError):
        EffectSpec("chase", (1,), ((256, 0, 0, 0),))


def test_unknown_kind_is_not_rendered_as_another(base: Canvas) -> None:
    """A kind without its own branches raises instead of rendering as twinkle or rainbow."""
    spec = EffectSpec("twinkle", LEDS)
    object.__setattr__(spec, "kind", "sparkle")  # past the check in __post_init__
    with pytest.raises(ValueError):
        loop_length(spec, FPS)
    with pytest.raises(ValueError):
        _effect_colors(spec, base, list(LEDS), 0.0, 1.0)
    with pytest.raises(ValueError):
        _cells("sparkle", (RED,))


def test_leds_outside_canvas(base: Canvas) -> None:
    for leds in ((400,), (-1, 5)):
        with pytest.raises(ValueError):
            render_frame(EffectSpec("chase", leds), base, 0, FPS)
