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
LEDS = tuple(range(37, 80))
FPS = 10


@pytest.fixture
def base() -> Canvas:
    canvas = Canvas(400)
    canvas.fill(SILVER)
    return canvas


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
    base = Canvas(400, [(i % 256, i // 256, 255 - i % 256, i * 7 % 256) for i in range(400)])
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
