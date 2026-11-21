"""Tests for colour parsing and wire packing."""

import pytest

from custom_components.twinkly_canvas.colors import (
    ColorError,
    blend,
    hsv_to_rgbw,
    parse_color,
    scale,
    to_wire,
)


def test_parse_named():
    assert parse_color("red") == (255, 0, 0, 0)
    assert parse_color(" Warm White ") == (0, 0, 0, 255)
    assert parse_color("warm_white") == (0, 0, 0, 255)
    assert parse_color("silver") == (192, 192, 192, 0)
    assert parse_color("gold") == (255, 215, 0, 0)


def test_parse_hex():
    assert parse_color("#FF8000") == (255, 128, 0, 0)
    assert parse_color("#ff800010") == (255, 128, 0, 16)


def test_parse_sequences():
    assert parse_color([1, 2, 3]) == (1, 2, 3, 0)
    assert parse_color((1, 2, 3, 4)) == (1, 2, 3, 4)


@pytest.mark.parametrize(
    "value", ["", "#12345", "#GGGGGG", [1, 2], [1, 2, 3, 4, 5], [256, 0, 0], [-1, 0, 0], "nope"]
)
def test_parse_errors(value):
    with pytest.raises(ColorError):
        parse_color(value)


def test_to_wire_order():
    assert to_wire((1, 2, 3, 4), "RGBW") == bytes([4, 1, 2, 3])
    assert to_wire((1, 2, 3, 4), "RGB") == bytes([1, 2, 3])


def test_blend_and_scale():
    assert blend((0, 0, 0, 0), (200, 100, 50, 0), 0.5) == (100, 50, 25, 0)
    assert scale((200, 100, 50, 10), 0.5) == (100, 50, 25, 5)
    assert scale((200, 100, 50, 10), 2) == (255, 200, 100, 20)
    assert blend((0, 0, 0, 0), (200, 0, 0, 0), 2) == (255, 0, 0, 0)
    assert scale((200, 0, 0, 0), -1) == (0, 0, 0, 0)


def test_hsv():
    assert hsv_to_rgbw(0.0, 1.0, 1.0) == (255, 0, 0, 0)
    assert hsv_to_rgbw(1 / 3, 1.0, 1.0) == (0, 255, 0, 0)
