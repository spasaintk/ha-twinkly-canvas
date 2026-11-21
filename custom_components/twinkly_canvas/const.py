"""Constants for the Twinkly Canvas integration."""

from datetime import timedelta

from homeassistant.const import CONF_HOST as CONF_HOST, Platform  # CONF_HOST: re-export

DOMAIN = "twinkly_canvas"
PLATFORMS = [Platform.LIGHT]

POLL_INTERVAL = timedelta(seconds=60)

MAX_FPS = 11
MAX_FRAMES_PER_MOVIE = 240
MOVIE_OVERHEAD_FRAMES = 4  # measured on a real device
MOVIE_MARGIN_FRAMES = 4
MOVIE_NAME_MAX = 15
DEFAULT_MAX_MOVIES = 15  # when the device reports no movie limit

LIVE_DEBOUNCE = 0.6
LIVE_COMMIT_AFTER = 3.0
FIND_SECONDS = 6.0

RT_TIMEOUT_SECONDS = 60  # the device leaves real-time mode after about 61 s
DELETE_NEEDS_MODE_CHANGE = True  # deleting the playing movie answers HTTP 412

MIN_FIRMWARE = "2.5.6"

EFFECT_PAINTED = "Painted"
EFFECT_CUSTOM = "Custom effect"
PREFIX_LOOK = "Look: "
PREFIX_BUILTIN = "Built-in "
PREFIX_APP = "App: "
