"""Tests for the LED selection grammar."""

import pytest

from custom_components.twinkly_canvas.selection import (
    SelectionError,
    format_selection,
    parse_selection,
)


def test_single_and_ranges():
    assert parse_selection("1-3, 7", 400) == [0, 1, 2, 6]


def test_reversed_range():
    assert parse_selection("5-3", 400) == [2, 3, 4]


def test_odd_even():
    assert parse_selection("38-80 odd", 400) == [n - 1 for n in range(39, 80, 2)]
    assert len(parse_selection("38-80 odd", 400)) == 21
    assert len(parse_selection("38-80 EVEN", 400)) == 22


def test_every():
    assert parse_selection("1-20 every 5", 400) == [0, 5, 10, 15]
    assert parse_selection("1-20 every 5 from 3", 400) == [2, 7, 12, 17]


def test_all():
    assert parse_selection("all", 400) == list(range(400))
    assert len(parse_selection("all even", 400)) == 200


def test_groups():
    assert parse_selection("corners, 10", 400, {"Corners": "1, 200"}) == [0, 9, 199]


def test_overlap():
    assert parse_selection("1-5, 3-7", 400) == list(range(7))


@pytest.mark.parametrize(
    "spec", ["0", "401", "1-401", "abc", "", "1-5 every 0", "2-2 odd", "unknown"]
)
def test_errors(spec):
    with pytest.raises(SelectionError):
        parse_selection(spec, 400)


def test_nested_group_rejected():
    with pytest.raises(SelectionError):
        parse_selection("b", 400, {"a": "1-3", "b": "a"})


def test_format_selection():
    assert format_selection([0, 1, 2, 6, 9, 10]) == "1-3, 7, 10-11"
    assert format_selection([]) == ""
