"""LED selection grammar: "1-3, 7, corners odd" -> 0-based indices.

A trailing filter (odd, even, every N, every N from M) applies to the whole selection;
on its own it applies to all LEDs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

GROUP_NAME_MAX = 40

_FILTER = re.compile(  # after a space or a comma: "1-20 odd", "1-20,odd"
    r"[\s,]\s*(odd|even|every\s+([0-9]{1,6})(?:\s+from\s+([0-9]{1,6}))?)\s*$",
    re.IGNORECASE,
)


class SelectionError(ValueError):
    """The selection text is invalid."""


def _number(text: str, led_count: int) -> int:
    if not text:
        raise SelectionError("Empty item: check for a stray comma or dash")
    if not re.fullmatch(r"[0-9]{1,6}", text):
        shown = text if len(text) <= 40 else f"{text[:40]}..."
        raise SelectionError(f"'{shown}' is not an LED number, range, 'all' or group")
    n = int(text)
    check_leds((n - 1,), led_count)
    return n


def check_leds(indices: Iterable[int], led_count: int) -> None:
    """Refuse 0-based indices outside the LEDs, naming them 1-based like a selection."""
    for i in indices:
        if not 0 <= i < led_count:
            raise SelectionError(f"LED {i + 1} is outside 1-{led_count}")


def parse_selection(
    spec: str, led_count: int, groups: Mapping[str, str] | None = None
) -> list[int]:
    """Return sorted unique 0-based indices for a selection spec."""
    spec = " ".join(spec.split())  # single spaces keep the filter search linear
    if len(spec) > 10000:
        raise SelectionError("Selection text is too long")
    if not spec:
        raise SelectionError("Selection is empty")
    if _FILTER.fullmatch(" " + spec):  # a filter on its own filters all LEDs
        spec = f"all {spec}"
    flt = _FILTER.search(spec)  # its match takes the comma of "1-20, odd" along
    spec = spec[: flt.start()] if flt else spec.removesuffix(",")  # "1-20,"
    lowered = {k.lower(): v for k, v in (groups or {}).items()}
    chosen: set[int] = set()  # 1-based numbers
    # Each item once, ignoring case: a group named a thousand times parses once.
    items = {p.strip().lower(): p.strip() for p in spec.split(",")}
    for key, part in items.items():
        if key == "all":
            chosen.update(range(1, led_count + 1))
        elif key in lowered:
            chosen.update(i + 1 for i in parse_selection(lowered[key], led_count))
        elif "-" in part:
            a, _, b = part.partition("-")
            lo, hi = sorted(
                (_number(a.strip(), led_count), _number(b.strip(), led_count))
            )
            chosen.update(range(lo, hi + 1))
        else:
            chosen.add(_number(part, led_count))
    if not chosen:
        raise SelectionError("Selection matches no LEDs")
    if flt:
        kind = flt.group(1).lower()
        if kind == "odd":
            chosen = {n for n in chosen if n % 2 == 1}
        elif kind == "even":
            chosen = {n for n in chosen if n % 2 == 0}
        else:
            step = int(flt.group(2))
            if step < 1:
                raise SelectionError("'every' needs a step of at least 1")
            start = int(flt.group(3)) if flt.group(3) else min(chosen)
            chosen = {n for n in chosen if n >= start and (n - start) % step == 0}
    if not chosen:
        raise SelectionError("Selection matches no LEDs")
    return [n - 1 for n in sorted(chosen)]


def validate_group_name(name: str) -> str:
    """The stripped name; refuses names that a selection would read as something else."""
    # selections collapse whitespace runs, so names must too
    name = " ".join(name.split())
    if not 1 <= len(name) <= GROUP_NAME_MAX:
        raise SelectionError(f"A group name needs 1 to {GROUP_NAME_MAX} characters")
    if name.lower() in ("all", "odd", "even"):
        raise SelectionError(f"'{name}' is a reserved word in selections")
    if re.fullmatch(r"[0-9 -]+", name):
        raise SelectionError(f"'{name}' reads as an LED number or a range")
    if "," in name:
        raise SelectionError("A group name cannot contain a comma")
    # the space also catches a name that is only a filter
    if _FILTER.search(" " + name):
        raise SelectionError(f"'{name}' ends in a filter (odd, even or every N)")
    return name


def format_selection(indices: Iterable[int]) -> str:
    """Compact 1-based text for 0-based indices, e.g. '1-3, 7'."""
    nums = sorted({i + 1 for i in indices})
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)
