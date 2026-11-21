"""LED selection grammar: "1-3, 7, corners, 38-80 odd" -> 0-based indices."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re

_FILTER = re.compile(
    r"\s+(odd|even|every\s+(\d+)(?:\s+from\s+(\d+))?)\s*$", re.IGNORECASE
)


class SelectionError(ValueError):
    """The selection text is invalid."""


def _number(text: str, led_count: int) -> int:
    if not text.isdigit():
        raise SelectionError(f"'{text}' is not an LED number, range, 'all' or group")
    n = int(text)
    if not 1 <= n <= led_count:
        raise SelectionError(f"LED {n} is outside 1-{led_count}")
    return n


def parse_selection(
    spec: str, led_count: int, groups: Mapping[str, str] | None = None
) -> list[int]:
    """Return sorted unique 0-based indices for a selection spec."""
    spec = spec.strip()
    if not spec:
        raise SelectionError("Selection is empty")
    flt = _FILTER.search(spec)
    if flt:
        spec = spec[: flt.start()]
    lowered = {k.lower(): v for k, v in (groups or {}).items()}
    chosen: set[int] = set()  # 1-based numbers
    for part in (p.strip() for p in spec.split(",")):
        if part.lower() == "all":
            chosen.update(range(1, led_count + 1))
        elif part.lower() in lowered:
            chosen.update(i + 1 for i in parse_selection(lowered[part.lower()], led_count))
        elif "-" in part:
            a, _, b = part.partition("-")
            lo, hi = sorted((_number(a.strip(), led_count), _number(b.strip(), led_count)))
            chosen.update(range(lo, hi + 1))
        else:
            chosen.add(_number(part, led_count))
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
