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


def test_b64_is_rgbw_per_led() -> None:
    """The card decodes R,G,B,W per LED: a contract, not an implementation detail."""
    assert Canvas(1, [(1, 2, 3, 4)]).to_b64() == "AQIDBA=="


def test_set_checks_every_index_before_changing_any() -> None:
    c = Canvas(2)
    with pytest.raises(ValueError):
        c.set([0, 2], (1, 2, 3, 4))
    assert c.pixels[0] == (0, 0, 0, 0)


@pytest.mark.parametrize(
    "color",
    [
        (1, 2, 3),
        (1, 2, 3, 4, 5),
        (256, 0, 0, 0),
        (-1, 0, 0, 0),
        (True, 0, 0, 0),
        (1.0, 0, 0, 0),
        "red",
        None,
    ],
)
def test_bad_color_rejected(color) -> None:
    c = Canvas(2)
    with pytest.raises(ValueError):
        c.set([0], color)
    with pytest.raises(ValueError):
        c.fill(color)
    assert c.pixels == [(0, 0, 0, 0)] * 2


def test_colors_stored_as_tuples() -> None:
    c = Canvas(2)
    c.set([0], [1, 2, 3, 4])
    assert c.pixels == [(1, 2, 3, 4), (0, 0, 0, 0)]
    c.fill([5, 6, 7, 8])
    assert c.pixels == [(5, 6, 7, 8)] * 2
    assert all(type(p) is tuple for p in c.pixels)


def test_pixels_must_match_led_count() -> None:
    with pytest.raises(ValueError):
        Canvas(2, [(0, 0, 0, 0)])
