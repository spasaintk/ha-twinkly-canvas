"""Movie storage planner: decides which of our own movies to delete before an upload."""

from __future__ import annotations

from dataclasses import dataclass

from .const import MOVIE_MARGIN_FRAMES, MOVIE_NAME_MAX, MOVIE_OVERHEAD_FRAMES


@dataclass(frozen=True)
class DeviceMovie:
    id: int
    unique_id: str  # lower-cased
    name: str
    frames: int
    fps: int


@dataclass(frozen=True)
class MovieState:
    movies: tuple[DeviceMovie, ...]
    available_frames: int
    max_capacity: int
    max_movies: int

    @classmethod
    def from_api(cls, data: dict, max_movies: int | None = None) -> MovieState:
        """Parse GET /xled/v1/movies; data["max"] wins over the max_movies fallback."""
        return cls(
            movies=tuple(
                DeviceMovie(
                    m["id"],
                    str(m["unique_id"]).lower(),
                    m.get("name", ""),
                    m["frames_number"],
                    m["fps"],
                )
                for m in data.get("movies", [])
            ),
            available_frames=data["available_frames"],
            max_capacity=data["max_capacity"],
            max_movies=data.get("max", max_movies),
        )


@dataclass(frozen=True)
class UploadPlan:
    delete: tuple[str, ...]
    fits: bool
    reason: str | None  # "memory_full" or "too_many_movies"


def _tail_run(state: MovieState, ours: set[str]) -> list[DeviceMovie]:
    own = {u.lower() for u in ours}
    run: list[DeviceMovie] = []
    for movie in reversed(state.movies):
        if movie.unique_id not in own:
            break
        run.append(movie)
    return run


def plan_upload(state: MovieState, ours: set[str], new_frames: int) -> UploadPlan:
    """Delete our trailing movies (last first) if that makes room for new_frames."""
    run = _tail_run(state, ours)
    freed = sum(m.frames + MOVIE_OVERHEAD_FRAMES for m in run)
    if state.available_frames + freed < new_frames + MOVIE_OVERHEAD_FRAMES + MOVIE_MARGIN_FRAMES:
        return UploadPlan((), False, "memory_full")
    if len(state.movies) - len(run) >= state.max_movies:
        return UploadPlan((), False, "too_many_movies")
    return UploadPlan(tuple(m.unique_id for m in run), True, None)


def stranded(state: MovieState, ours: set[str]) -> int:
    """Our movies that sit before an app movie and so cannot be deleted."""
    own = {u.lower() for u in ours}
    return sum(m.unique_id in own for m in state.movies) - len(_tail_run(state, ours))


def movie_name(counter: int) -> str:
    return f"Canvas {counter % 10**8}"[:MOVIE_NAME_MAX]
