"""Integration classes and constants."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry

DOMAIN = "view_assist"
URL_BASE = "/view_assist"
JSMODULES = [
    {
        "filename": "view_assist.js",
        "version": "1.0.0",
    },
]

type VAConfigEntry = ConfigEntry[RuntimeData]


class VAMode(StrEnum):
    """View Assist modes."""

    NORMAL = "normal"


class VAType(StrEnum):
    """Sensor type enum."""

    VIEW_AUDIO = "view_audio"
    AUDIO_ONLY = "audio_only"


class VAAssistPrompt(StrEnum):
    """Assist prompt types enum."""

    BLUR_POPUP = "blur pop up"
    FLASHING_BAR = "flashing bar"


class VAIconSizes(StrEnum):
    """Icon size options enum."""

    SMALL = "6vw"
    MEDIUM = "7vw"
    LARGE = "8vw"


class VAMicType(StrEnum):
    """Mic types."""

    HA_VOICE_SATELLITE = "Home Assistant Voice Satellite"
    HASS_MIC = "HassMic"
    STREAM_ASSIST = "Stream Assist"


class VADisplayType(StrEnum):
    """Display types."""

    BROWSERMOD = "BrowserMod"
    REMOTE_ASSIST_DISPLAY = "Remote Assist Display"


class TimerClass(StrEnum):
    """Timer class."""

    ALARM = "alarm"
    REMINDER = "reminder"
    TIMER = "timer"


@dataclass
class TimerInterval:
    """Timer Interval."""

    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0


@dataclass
class TimerTime:
    """Timer Time."""

    day: str = ""
    hour: int = 0
    minute: int = 0
    second: int = 0
    meridiem: str = ""


class TimerStatus(StrEnum):
    """Timer status."""

    INACTIVE = "inactive"
    RUNNING = "running"
    EXPIRED = "expired"


@dataclass
class Timer:
    """Class to hold timer."""

    timer_class: TimerClass
    expires_at: int
    name: str | None = None
    device_id: str | None = None
    created_at: int = 0
    updated_at: int = 0
    status: TimerStatus = field(default_factory=TimerStatus.INACTIVE)
    extra_info: dict[str, Any] | None = None


# Config keys
CONF_MIC_DEVICE = "mic_device"
CONF_MEDIAPLAYER_DEVICE = "mediaplayer_device"
CONF_MUSICPLAYER_DEVICE = "musicplayer_device"
CONF_DISPLAY_DEVICE = "display_device"
CONF_BROWSER_ID = "browser_id"
CONF_DASHBOARD = "dashboard"
CONF_HOME = "home"
CONF_INTENT = "intent"
CONF_MUSIC = "music"
CONF_BACKGROUND = "background"
CONF_ASSIST_PROMPT = "assist_prompt"
CONF_STATUS_ICON_SIZE = "status_icons_size"
CONF_FONT_STYLE = "font_style"
CONF_STATUS_ICONS = "status_icons"
CONF_USE_24H_TIME = "use_24_hour_time"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_MIC_TYPE = "mic_type"
CONF_DISPLAY_TYPE = "display_type"
CONF_DISPLAY_DEVICE = "display_device"
CONF_VIEW_TIMEOUT = "view_timeout"
CONF_DO_NOT_DISTURB = "do_not_disturb"
CONF_USE_ANNOUNCE = "use_announce"
CONF_MIC_UNMUTE = "micunmute"
CONF_TIME = "time"
CONF_TIMER_ID = "timer_id"
CONF_REMOVE_ALL = "remove_all"

# Config default values
DEFAULT_NAME = "View Assist"
DEFAULT_TYPE = VAType.VIEW_AUDIO
DEFAULT_DASHBOARD = "/view-assist"
DEFAULT_VIEW_HOME = "/view-assist/clock"
DEFAULT_VIEW_MUSIC = "/view-assist/music"
DEFAULT_VIEW_INTENT = "/view-assist/intent"
DEFAULT_VIEW_BACKGROUND = "/local/viewassist/backgrounds/mybackground.jpg"
DEFAULT_ASSIST_PROMPT = VAAssistPrompt.BLUR_POPUP
DEFAULT_STATUS_ICON_SIZE = VAIconSizes.LARGE
DEFAULT_FONT_STYLE = "Roboto"
DEFAULT_STATUS_ICONS = []
DEFAULT_USE_24H_TIME = False
DEFAULT_WEATHER_ENITITY = "weather.home"
DEFAULT_MIC_TYPE = VAMicType.HA_VOICE_SATELLITE
DEFAULT_DISPLAY_TYPE = VADisplayType.BROWSERMOD
DEFAULT_MODE = "normal"
DEFAULT_VIEW_TIMEOUT = 20
DEFAULT_DND = False
DEFAULT_USE_ANNOUNCE = True
DEFAULT_MIC_UNMUTE = False

VA_ATTRIBUTE_UPDATE_EVENT = "va_attr_update_event_{}"
VA_TIMER_FINISHED_EVENT = "va_timer_finished"

TIMERS_STORE_NAME = f"{DOMAIN}.timers"

WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

SPECIAL_DAYS = [
    "today",
    "tomorrow",
]

AMPM = ["am", "pm"]

HOURS = {
    "midnight": 0,
    "noon": 12,
}
PAST_TO = {
    "quarter": 15,
    "half": 30,
}
HOUR_FRACTIONS = {
    "1/4": 15,
    "quarter": 15,
    "1/2": 30,
    "half": 30,
    "3/4": 45,
    "three quarters": 45,
}


class RuntimeData:
    """Class to hold your data."""

    def __init__(self) -> None:
        """Initialise runtime data."""
        # Runtime variables go here
        self._timers = None

        # Default config
        self.type: VAType | None = None
        self.name: str = ""
        self.mic_device: str = ""
        self.mediaplayer_device: str = ""
        self.musicplayer_device: str = ""
        self.display_device: str = ""
        self.browser_id: str = ""

        # Dashboard options
        self.dashboard: str = DEFAULT_DASHBOARD
        self.home: str = DEFAULT_VIEW_HOME
        self.music: str = DEFAULT_VIEW_MUSIC
        self.intent: str = DEFAULT_VIEW_INTENT
        self.background: str = DEFAULT_VIEW_BACKGROUND
        self.assist_prompt: VAAssistPrompt = DEFAULT_ASSIST_PROMPT
        self.status_icons_size: VAIconSizes = DEFAULT_STATUS_ICON_SIZE
        self.font_style: str = DEFAULT_FONT_STYLE
        self.status_icons: list[str] = DEFAULT_STATUS_ICONS
        self.use_24_hour_time: bool = DEFAULT_USE_24H_TIME

        # Default options
        self.weather_entity: str = DEFAULT_WEATHER_ENITITY
        self.mic_type: VAMicType = DEFAULT_MIC_TYPE
        self.display_type: VADisplayType = DEFAULT_DISPLAY_TYPE
        self.mode: str = DEFAULT_MODE
        self.view_timeout: int = DEFAULT_VIEW_TIMEOUT
        self.do_not_disturb: bool = DEFAULT_DND
        self.use_announce: bool = DEFAULT_USE_ANNOUNCE
        self.mic_unmute: bool = DEFAULT_MIC_UNMUTE

        # Extra data for holding key/value pairs passed in by set_state service call
        self.extra_data: dict[str, Any] = {}
