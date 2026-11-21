"""Tests for the LED selection grammar."""

import time
from unittest.mock import patch

import pytest

from custom_components.twinkly_canvas import selection
from custom_components.twinkly_canvas.selection import (
    SelectionError,
    format_selection,
    parse_selection,
    validate_group_name,
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


@pytest.mark.parametrize("flt", ["even", "odd", "every 3", "every 3 from 2", " ODD "])
def test_filter_alone_selects_all(flt):
    assert parse_selection(flt, 400) == parse_selection(f"all {flt}", 400)


def test_groups():
    assert parse_selection("corners, 10", 400, {"Corners": "1, 200"}) == [0, 9, 199]


def test_overlap():
    assert parse_selection("1-5, 3-7", 400) == list(range(7))


def test_trailing_comma_dropped():
    assert parse_selection("1-20, odd", 400) == list(range(0, 20, 2))
    assert parse_selection("1-20,", 400) == list(range(20))


def test_filter_after_comma_without_space():
    assert parse_selection("1-20,odd", 400) == parse_selection("1-20 odd", 400)
    assert parse_selection("1-20,every 3", 400) == [0, 3, 6, 9, 12, 15, 18]


def test_group_named_often_parses_once():
    """Each group parses once per call, however often the text names it."""
    group = ", ".join(f"{n}-{n + 270}" for n in range(1, 125))
    assert len(group) >= 1000
    start = time.perf_counter()
    assert parse_selection(", ".join(["g"] * 2000), 400, {"g": group}) == list(
        range(394)
    )
    assert time.perf_counter() - start < 0.5


def test_group_named_often_parses_once_counted():
    """The timing test's deterministic companion: the group's own parse runs once."""
    with patch.object(selection, "parse_selection", wraps=parse_selection) as group:
        spec = ", ".join(["g", " G "] * 1000)  # the outer call is not patched
        assert parse_selection(spec, 400, {"g": "1-10"}) == list(range(10))
    assert group.call_count == 1


def test_error_quotes_at_most_40_characters():
    item = "abcdefghij" * 20
    with pytest.raises(SelectionError) as err:
        parse_selection(item, 400)
    assert len(str(err.value)) < 120
    assert f"'{item[:40]}...'" in str(err.value)


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ("0", "outside"),
        ("401", "outside"),
        ("1-401", "outside"),
        ("abc", "not an LED number"),
        ("", "empty"),
        ("1-5 every 0", "at least 1"),
        ("2-2 odd", "no LEDs"),
        ("unknown", "not an LED number"),
        ("²", "not an LED number"),
        ("1-²", "not an LED number"),
        ("①", "not an LED number"),
        ("9" * 5000, "not an LED number"),
        ("1 every " + "9" * 5000, "not an LED number"),
        ("1,,2", "Empty item"),
        (",1", "Empty item"),
        ("-5", "Empty item"),
        ("5-", "Empty item"),
        ("1" * 10001, "too long"),
    ],
)
def test_errors(spec, match):
    with pytest.raises(SelectionError, match=match):
        parse_selection(spec, 400)


def test_nothing_to_filter():
    with pytest.raises(SelectionError, match="no LEDs"):
        parse_selection("all every 5", 0)


def test_long_whitespace_is_fast():
    """Whitespace runs collapse before the filter search, which stays linear."""
    start = time.perf_counter()
    assert parse_selection("1" + " " * 50000 + "odd", 400) == [0]
    with pytest.raises(SelectionError):
        parse_selection("1" + " " * 50000 + "2", 400)
    assert time.perf_counter() - start < 1


def test_nested_group_rejected():
    with pytest.raises(SelectionError):
        parse_selection("b", 400, {"a": "1-3", "b": "a"})


def test_format_selection():
    assert format_selection([0, 1, 2, 6, 9, 10]) == "1-3, 7, 10-11"
    assert format_selection([]) == ""


def test_validate_group_name():
    assert validate_group_name(" Corners ") == "Corners"
    assert validate_group_name("left-side") == "left-side"
    assert validate_group_name("x" * 40) == "x" * 40


@pytest.mark.parametrize(
    ("name", "match"),
    [
        ("all", "reserved"),
        ("ODD", "reserved"),
        ("5", "number or a range"),
        ("1-3", "number or a range"),
        ("1 - 3", "number or a range"),
        ("a,b", "comma"),
        ("left odd", "filter"),
        ("top every 2", "filter"),
        ("top EVERY 2 from 3", "filter"),
        ("every 5", "filter"),
        ("", "1 to 40 characters"),
        ("   ", "1 to 40 characters"),
        ("x" * 41, "1 to 40 characters"),
    ],
)
def test_validate_group_name_rejects(name, match):
    with pytest.raises(SelectionError, match=match):
        validate_group_name(name)
