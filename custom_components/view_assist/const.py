"""Integration classes and constants."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry

DOMAIN = "view_assist"
GITHUB_REPO = "dinki/View-Assist"
GITHUB_BRANCH = "main"
GITHUB_TOKEN_FILE = "github.token"
GITHUB_PATH = "View Assist dashboard and views"
VIEWS_DIR = "views"
COMMUNITY_VIEWS_DIR = "community_contributions"
DASHBOARD_DIR = "dashboard"

DASHBOARD_NAME = "View Assist"
DEFAULT_VIEW = "clock"
DEFAULT_VIEWS = [
    "alarm",
    "camera",
    "clock",
    "info",
    "infopic",
    "intent",
    "list",
    "locate",
    "music",
    "sports",
    "thermostat",
    "weather",
    "webpage",
]
CYCLE_VIEWS = ["music", "info", "weather", "clock"]

BROWSERMOD_DOMAIN = "browser_mod"
REMOTE_ASSIST_DISPLAY_DOMAIN = "remote_assist_display"
CUSTOM_CONVERSATION_DOMAIN = "custom_conversation"
USE_VA_NAVIGATION_FOR_BROWSERMOD = True

IMAGE_PATH = "images"
AUDIO_PATH = "audio"
VA_SUB_DIRS = [AUDIO_PATH, IMAGE_PATH]
URL_BASE = "view_assist"
RANDOM_IMAGE_URL = "https://unsplash.it/1280/800?random"
JSMODULES = [
    {
        "name": "View Assist Helper",
        "filename": "view_assist.js",
        "version": "1.0.10",
    },
]


type VAConfigEntry = ConfigEntry[RuntimeData]


class VAMode(StrEnum):
    """View Assist modes."""

    NORMAL = "normal"
    MUSIC = "music"
    CYCLE = "cycle"
    HOLD = "hold"
    NIGHT = "night"
    ROTATE = "rotate"


VAMODE_REVERTS = {
    VAMode.NORMAL: {"revert": True, "view": "home"},
    VAMode.MUSIC: {"revert": True, "view": "music"},
    VAMode.CYCLE: {"revert": False},
    VAMode.HOLD: {"revert": False},
    VAMode.NIGHT: {"revert": True, "view": "home"},
}


class VAType(StrEnum):
    """Sensor type enum."""

    MASTER_CONFIG = "master_config"
    VIEW_AUDIO = "view_audio"
    AUDIO_ONLY = "audio_only"


class VAAssistPrompt(StrEnum):
    """Assist prompt types enum."""

    BLUR_POPUP = "blur_pop_up"
    FLASHING_BAR = "flashing_bar"


class VAIconSizes(StrEnum):
    """Icon size options enum."""

    SMALL = "6vw"
    MEDIUM = "7vw"
    LARGE = "8vw"


class VADisplayType(StrEnum):
    """Display types."""

    BROWSERMOD = "browser_mod"
    REMOTE_ASSIST_DISPLAY = "remote_assist_display"


# Config keys
CONF_MIC_DEVICE = "mic_device"
CONF_MEDIAPLAYER_DEVICE = "mediaplayer_device"
CONF_MUSICPLAYER_DEVICE = "musicplayer_device"
CONF_DISPLAY_DEVICE = "display_device"
CONF_INTENT_DEVICE = "intent_device"
CONF_DASHBOARD = "dashboard"
CONF_HOME = "home"
CONF_INTENT = "intent"
CONF_LIST = "list_view"
CONF_MUSIC = "music"
CONF_BACKGROUND = "background"
CONF_ASSIST_PROMPT = "assist_prompt"
CONF_STATUS_ICON_SIZE = "status_icons_size"
CONF_FONT_STYLE = "font_style"
CONF_STATUS_ICONS = "status_icons"
CONF_USE_24H_TIME = "use_24_hour_time"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_VIEW_TIMEOUT = "view_timeout"
CONF_DO_NOT_DISTURB = "do_not_disturb"
CONF_USE_ANNOUNCE = "use_announce"
CONF_MIC_UNMUTE = "micunmute"
CONF_DEV_MIMIC = "dev_mimic"
CONF_HIDE_HEADER = "hide_header"
CONF_HIDE_SIDEBAR = "hide_sidebar"
CONF_ROTATE_BACKGROUND = "rotate_background"
CONF_ROTATE_BACKGROUND_SOURCE = "rotate_background_source"
CONF_ROTATE_BACKGROUND_PATH = "rotate_background_path"
CONF_ROTATE_BACKGROUND_LINKED_ENTITY = "rotate_background_linked_entity"
CONF_ROTATE_BACKGROUND_INTERVAL = "rotate_background_interval"
CONF_MIC_TYPE = "mic_type"

# Config default values
DEFAULT_NAME = "View Assist"
DEFAULT_TYPE = VAType.VIEW_AUDIO
DEFAULT_DASHBOARD = "/view-assist"
DEFAULT_VIEW_HOME = "/view-assist/clock"
DEFAULT_VIEW_MUSIC = "/view-assist/music"
DEFAULT_VIEW_INTENT = "/view-assist/intent"
DEFAULT_VIEW_LIST = "/view-assist/list"
DEFAULT_VIEW_INFO = "info"
DEFAULT_VIEW_BACKGROUND = "/view_assist/dashboard/background.jpg"
DEFAULT_ASSIST_PROMPT = VAAssistPrompt.BLUR_POPUP
DEFAULT_STATUS_ICON_SIZE = VAIconSizes.LARGE
DEFAULT_FONT_STYLE = "Roboto"
DEFAULT_STATUS_ICONS = []
DEFAULT_USE_24H_TIME = False
DEFAULT_WEATHER_ENITITY = "weather.home"
DEFAULT_MODE = "normal"
DEFAULT_VIEW_TIMEOUT = 20
DEFAULT_DND = False
DEFAULT_USE_ANNOUNCE = True
DEFAULT_MIC_UNMUTE = False
DEFAULT_HIDE_SIDEBAR = True
DEFAULT_HIDE_HEADER = True
DEFAULT_ROTATE_BACKGROUND = False
DEFAULT_ROTATE_BACKGROUND_SOURCE = "local_sequence"
DEFAULT_ROTATE_BACKGROUND_PATH = f"{IMAGE_PATH}/backgrounds"
DEFAULT_ROTATE_BACKGROUND_INTERVAL = 60

# Service attributes
ATTR_EVENT_NAME = "event_name"
ATTR_EVENT_DATA = "event_data"
ATTR_PATH = "path"
ATTR_DEVICE = "device"
ATTR_REDOWNLOAD_FROM_REPO = "download_from_repo"
ATTR_COMMUNITY_VIEW = "community_view"
ATTR_BACKUP_CURRENT_VIEW = "backup_current_view"
ATTR_EXTRA = "extra"
ATTR_TYPE = "type"
ATTR_TIMER_ID = "timer_id"
ATTR_REMOVE_ALL = "remove_all"
ATTR_INCLUDE_EXPIRED = "include_expired"
ATTR_MEDIA_FILE = "media_file"
ATTR_RESUME_MEDIA = "resume_media"
ATTR_MAX_REPEATS = "max_repeats"

VA_ATTRIBUTE_UPDATE_EVENT = "va_attr_update_event_{}"
VA_BACKGROUND_UPDATE_EVENT = "va_background_update_{}"
CC_CONVERSATION_ENDED_EVENT = f"{CUSTOM_CONVERSATION_DOMAIN}_conversation_ended"


class RuntimeData:
    """Class to hold your data."""

    def __init__(self) -> None:
        """Initialise runtime data."""

        # Default config
        self.type: VAType | None = None
        self.name: str = ""
        self.mic_device: str = ""
        self.mediaplayer_device: str = ""
        self.musicplayer_device: str = ""
        self.display_device: str = ""
        self.intent_device: str = ""
        self.dev_mimic: bool = False

        # Dashboard options
        self.dashboard: str = DEFAULT_DASHBOARD
        self.home: str = DEFAULT_VIEW_HOME
        self.music: str = DEFAULT_VIEW_MUSIC
        self.intent: str = DEFAULT_VIEW_INTENT
        self.list_view: str = DEFAULT_VIEW_LIST
        self.background: str = DEFAULT_VIEW_BACKGROUND
        self.rotate_background: bool = False
        self.rotate_background_source: str = "local"
        self.rotate_background_path: str = ""
        self.rotate_background_linked_entity: str = ""
        self.rotate_background_interval: int = 60
        self.assist_prompt: VAAssistPrompt = DEFAULT_ASSIST_PROMPT
        self.status_icons_size: VAIconSizes = DEFAULT_STATUS_ICON_SIZE
        self.font_style: str = DEFAULT_FONT_STYLE
        self.status_icons: list[str] = DEFAULT_STATUS_ICONS
        self.use_24_hour_time: bool = DEFAULT_USE_24H_TIME

        # Default options
        self.weather_entity: str = DEFAULT_WEATHER_ENITITY
        self.mode: str = DEFAULT_MODE
        self.view_timeout: int = DEFAULT_VIEW_TIMEOUT
        self.hide_sidebar: bool = DEFAULT_HIDE_SIDEBAR
        self.hide_header: bool = DEFAULT_HIDE_HEADER
        self.do_not_disturb: bool = DEFAULT_DND
        self.use_announce: bool = DEFAULT_USE_ANNOUNCE
        self.mic_unmute: bool = DEFAULT_MIC_UNMUTE

        # Extra data for holding key/value pairs passed in by set_state service call
        self.extra_data: dict[str, Any] = {}


@dataclass
class VAEvent:
    """View Assist event."""

    event_name: str
    payload: dict | None = None


# TODO: Remove this when BP/Views updated
OPTION_KEY_MIGRATIONS = {
    "blur pop up": "blur_pop_up",
    "flashing bar": "flashing_bar",
    "Home Assistant Voice Satellite": "home_assistant_voice_satellite",
    "HassMic": "hassmic",
    "Stream Assist": "stream_assist",
    "BrowserMod": "browser_mod",
    "Remote Assist Display": "remote_assist_display",
}
