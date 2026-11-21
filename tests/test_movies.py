"""Tests for the movie storage planner."""

from custom_components.twinkly_canvas.movies import (
    DeviceMovie,
    MovieState,
    movie_name,
    plan_upload,
    stranded,
)


def _m(i: int, uid: str, frames: int = 10) -> DeviceMovie:
    return DeviceMovie(i, uid.lower(), f"m{i}", frames, 11)


def _state(movies, available=100, max_movies=55) -> MovieState:
    return MovieState(tuple(movies), available, 2536, max_movies)


def test_deletes_only_ours_at_tail():
    st = _state([_m(0, "a-uid"), _m(1, "b-uid"), _m(2, "c-uid")])
    plan = plan_upload(st, {"b-uid", "c-uid"}, 10)
    assert plan.delete == ("c-uid", "b-uid")


def test_stops_at_app_movie():
    st = _state([_m(0, "a-uid"), _m(1, "b-uid"), _m(2, "c-uid")])
    ours = {"a-uid", "c-uid"}
    assert plan_upload(st, ours, 10).delete == ("c-uid",)
    assert stranded(st, ours) == 1


def test_nothing_to_delete_when_last_is_app():
    st = _state([_m(0, "a-uid"), _m(1, "b-uid")])
    assert plan_upload(st, {"a-uid"}, 10).delete == ()


def test_plan_refuses_when_full():
    plan = plan_upload(_state([], available=10), set(), 100)
    assert plan.fits is False
    assert plan.reason == "memory_full"
    assert plan.delete == ()


def test_plan_counts_freed_frames():
    st = _state([_m(0, "a-uid", 10)], available=100)
    plan = plan_upload(st, {"a-uid"}, 100)
    assert plan.fits is True
    assert plan.delete == ("a-uid",)


def test_plan_refuses_at_max_movies():
    st = _state([_m(i, f"u{i}") for i in range(55)], max_movies=55)
    plan = plan_upload(st, set(), 1)
    assert plan.fits is False
    assert plan.reason == "too_many_movies"
    assert plan.delete == ()


def test_unique_id_case_insensitive():
    st = _state([_m(0, "abc")])
    assert plan_upload(st, {"ABC"}, 10).delete == ("abc",)


def test_from_api():
    st = MovieState.from_api(
        {
            "movies": [
                {
                    "id": 0,
                    "unique_id": "ABC",
                    "name": "n",
                    "frames_number": 5,
                    "fps": 11,
                    "descriptor_type": "rgbw_raw",
                    "leds_per_frame": 400,
                }
            ],
            "available_frames": 2124,
            "max_capacity": 2536,
            "max": 55,
        }
    )
    assert st == MovieState((DeviceMovie(0, "abc", "n", 5, 11),), 2124, 2536, 55)


def test_from_api_max_fallback():
    st = MovieState.from_api({"movies": [], "available_frames": 1, "max_capacity": 2}, 40)
    assert st.max_movies == 40


def test_movie_name():
    assert movie_name(7) == "Canvas 7"
    assert len(movie_name(99999999)) <= 15
