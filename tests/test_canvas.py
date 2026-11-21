"""Tests for the Canvas model."""

import pytest

from custom_components.twinkly_canvas.canvas import Canvas


def test_new_canvas_is_off() -> None:
    assert Canvas(4).pixels == [(0, 0, 0, 0)] * 4


def test_set_and_fill() -> None:
    c = Canvas(4)
    c.set([1, 3], (9, 8, 7, 6))
    assert c.pixels == [(0, 0, 0, 0), (9, 8, 7, 6), (0, 0, 0, 0), (9, 8, 7, 6)]
    c.fill((1, 1, 1, 1))
    assert c.pixels == [(1, 1, 1, 1)] * 4


def test_to_frame_rgbw() -> None:
    c = Canvas(2, [(1, 2, 3, 4), (5, 6, 7, 8)])
    assert c.to_frame("RGBW") == bytes([4, 1, 2, 3, 8, 5, 6, 7])


def test_b64_roundtrip() -> None:
    c = Canvas(2, [(1, 2, 3, 4), (5, 6, 7, 8)])
    assert Canvas.from_b64(2, c.to_b64()).pixels == c.pixels


def test_from_b64_wrong_length() -> None:
    c = Canvas(2)
    with pytest.raises(ValueError):
        Canvas.from_b64(3, c.to_b64())


def test_set_rejects_out_of_range() -> None:
    c = Canvas(2)
    with pytest.raises(ValueError):
        c.set([2], (1, 1, 1, 1))
    with pytest.raises(ValueError):
        c.set([-1], (1, 1, 1, 1))


def test_uniform_color() -> None:
    assert Canvas(3, [(1, 2, 3, 4)] * 3).uniform_color() == (1, 2, 3, 4)
    assert Canvas(2, [(1, 2, 3, 4), (0, 0, 0, 0)]).uniform_color() is None


def test_copy_is_independent() -> None:
    c = Canvas(2)
    d = c.copy()
    d.set([0], (1, 1, 1, 1))
    assert c.pixels[0] == (0, 0, 0, 0)
