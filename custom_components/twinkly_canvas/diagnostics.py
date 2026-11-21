"""Diagnostics: what the device reports and what we hold, with identifiers redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import TwinklyCanvasConfigEntry
from .movies import stranded

TO_REDACT = {
    "host", "mac", "uuid", "hw_id", "device_name", "unique_id", "name", "copyright", "ip",
    "listed_uid",  # the device-spelled movie id, same data as unique_id
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TwinklyCanvasConfigEntry
) -> dict[str, Any]:
    """Entry data, device info and state, plus store counts only."""
    data = entry.runtime_data
    store = data.store
    movies = data.coordinator.data.movies
    state = asdict(data.coordinator.data)
    # async_redact_data does not descend into tuples, so the movies become a list
    state["movies"]["movies"] = list(state["movies"]["movies"])
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "gestalt": asdict(data.info),
            "state": state,
            "last_update_success": data.coordinator.last_update_success,
            "store": {
                "groups": len(store.groups),
                "looks": len(store.looks),
                "our_movies": len(store.our_movies),
                "stranded": stranded(movies, set(store.our_movies)),
            },
        },
        TO_REDACT,
    )
