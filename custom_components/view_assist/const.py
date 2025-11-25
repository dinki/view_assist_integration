"""Integration classes and constants."""

from enum import StrEnum

from homeassistant.const import CONF_MODE, Platform

from .typed import (
    VABackgroundMode,
    VAIconSizes,
    VAMenuConfig,
    VAScreenMode,
    VATimeFormat,
)

PLATFORMS: list[Platform] = [Platform.SENSOR]

DOMAIN = "view_assist"
GITHUB_REPO = "dinki/View-Assist"
GITHUB_BRANCH = "main"
GITHUB_DEV_BRANCH = "dev"
GITHUB_TOKEN_FILE = "github.token"
DASHBOARD_VIEWS_GITHUB_PATH = "View Assist dashboard and views"
BLUEPRINT_GITHUB_PATH = "View_Assist_custom_sentences"
VIEWS_DIR = "views"
COMMUNITY_VIEWS_DIR = "community_contributions"
DASHBOARD_DIR = "dashboard"
DASHBOARD_NAME = "View Assist"
WIKI_URL = "https://dinki.github.io/View-Assist"

DEFAULT_VIEW = "clock"
CYCLE_VIEWS = ["music", "info", "weather", "clock"]

BROWSERMOD_DOMAIN = "browser_mod"
REMOTE_ASSIST_DISPLAY_DOMAIN = "remote_assist_display"
CUSTOM_CONVERSATION_DOMAIN = "custom_conversation"
MUSIC_ASSISTANT_DOMAIN = "music_assistant"
ESPHOME_DOMAIN = "esphome"
HASSMIC_DOMAIN = "hassmic"
VACA_DOMAIN = "vaca"
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
        "version": "1.0.24",
    },
]
# mins between checks for updated versions of dashboard and views
VERSION_CHECK_INTERVAL = 120


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


# Config keys
DATA = "data"
MASTER_CONFIG = "master_config"
DEVICES = "devices"
CONF_VA_BROWSER_IDS = "va_browser_ids"
CONF_MIC_DEVICE = "mic_device"
CONF_MEDIAPLAYER_DEVICE = "mediaplayer_device"
CONF_MUSICPLAYER_DEVICE = "musicplayer_device"
CONF_DISPLAY_DEVICE = "display_device"
CONF_INTENT_DEVICE = "intent_device"
CONF_ORIENTATION_SENSOR = "orientation_sensor"

CONF_DASHBOARD = "dashboard"
CONF_HOME = "home"
CONF_INTENT = "intent"
CONF_LIST = "list_view"
CONF_MUSIC = "music"
CONF_BACKGROUND_SETTINGS = "background_settings"
CONF_BACKGROUND_MODE = "background_mode"
CONF_BACKGROUND = "background"
CONF_ROTATE_BACKGROUND_PATH = "rotate_background_path"
CONF_ROTATE_BACKGROUND_LINKED_ENTITY = "rotate_background_linked_entity"
CONF_ROTATE_BACKGROUND_INTERVAL = "rotate_background_interval"

CONF_DISPLAY_SETTINGS = "display_settings"
CONF_ASSIST_PROMPT = "assist_prompt"
CONF_STATUS_ICON_SIZE = "status_icons_size"
CONF_FONT_STYLE = "font_style"
CONF_STATUS_ICONS = "status_icons"
CONF_MENU_CONFIG = "menu_config"
CONF_MENU_ITEMS = "menu_items"
CONF_MENU_TIMEOUT = "menu_timeout"
CONF_TIME_FORMAT = "time_format"
CONF_SCREEN_MODE = "screen_mode"

CONF_WEATHER_ENTITY = "weather_entity"
CONF_VIEW_TIMEOUT = "view_timeout"
CONF_DO_NOT_DISTURB = "do_not_disturb"
CONF_USE_ANNOUNCE = "use_announce"
CONF_MIC_UNMUTE = "micunmute"
CONF_DUCKING_VOLUME = "ducking_volume"
CONF_MUSIC_MODE_AUTO = "music_mode_auto"
CONF_MUSIC_MODE_TIMEOUT = "music_mode_timeout"

CONF_ENABLE_UPDATES = "enable_updates"
CONF_TRANSLATION_ENGINE = "translation_engine"
CONF_DEVELOPER_DEVICE = "developer_device"
CONF_DEVELOPER_MIMIC_DEVICE = "developer_mimic_device"


# Legacy
CONF_MIC_TYPE = "mic_type"
CONF_USE_24H_TIME = "use_24_hour_time"
CONF_DEV_MIMIC = "dev_mimic"
CONF_HIDE_HEADER = "hide_header"
CONF_HIDE_SIDEBAR = "hide_sidebar"
CONF_ROTATE_BACKGROUND = "rotate_background"
CONF_ROTATE_BACKGROUND_SOURCE = "rotate_background_source"


DEFAULT_VALUES = {
    # Dashboard options
    CONF_DASHBOARD: "/view-assist",
    CONF_HOME: "/view-assist/clock",
    CONF_MUSIC: "/view-assist/music",
    CONF_INTENT: "/view-assist/intent",
    CONF_LIST: "/view-assist/list",
    CONF_BACKGROUND_SETTINGS: {
        CONF_BACKGROUND_MODE: VABackgroundMode.DEFAULT_BACKGROUND,
        CONF_BACKGROUND: "/view_assist/dashboard/background.jpg",
        CONF_ROTATE_BACKGROUND_PATH: f"{IMAGE_PATH}/backgrounds",
        CONF_ROTATE_BACKGROUND_LINKED_ENTITY: "",
        CONF_ROTATE_BACKGROUND_INTERVAL: 60,
    },
    CONF_DISPLAY_SETTINGS: {
        CONF_ASSIST_PROMPT: "blur_pop_up",
        CONF_STATUS_ICON_SIZE: VAIconSizes.LARGE,
        CONF_FONT_STYLE: "Roboto",
        CONF_STATUS_ICONS: [],
        CONF_MENU_CONFIG: VAMenuConfig.DISABLED,
        CONF_MENU_ITEMS: ["home", "weather"],
        CONF_MENU_TIMEOUT: 10,
        CONF_TIME_FORMAT: VATimeFormat.HOUR_12,
        CONF_SCREEN_MODE: VAScreenMode.HIDE_HEADER_SIDEBAR,
    },
    # Default options
    CONF_WEATHER_ENTITY: "weather.home",
    CONF_MODE: VAMode.NORMAL,
    CONF_VIEW_TIMEOUT: 20,
    CONF_DO_NOT_DISTURB: "off",
    CONF_USE_ANNOUNCE: "off",
    CONF_MIC_UNMUTE: "off",
    CONF_DUCKING_VOLUME: 70,
    CONF_MUSIC_MODE_AUTO: False,
    CONF_MUSIC_MODE_TIMEOUT: 300,
    # Default integration options
    CONF_ENABLE_UPDATES: True,
    # Default developer otions
    CONF_DEVELOPER_DEVICE: "",
    CONF_DEVELOPER_MIMIC_DEVICE: "",
}

# Config default values
DEFAULT_NAME = "View Assist"
DEFAULT_TYPE = "vaca"
DEFAULT_VIEW_INFO = "info"


# Service attributes
ATTR_EVENT_NAME = "event_name"
ATTR_EVENT_DATA = "event_data"

ATTR_DEVICE = "device"
ATTR_EXTRA = "extra"
ATTR_TYPE = "type"

ATTR_LANGUAGE = "language"
ATTR_TIMER_ID = "timer_id"
ATTR_REMOVE_ALL = "remove_all"
ATTR_INCLUDE_EXPIRED = "include_expired"
ATTR_MEDIA_FILE = "media_file"
ATTR_RESUME_MEDIA = "resume_media"
ATTR_MAX_REPEATS = "max_repeats"
ATTR_ASSET_CLASS = "asset_class"
ATTR_BACKUP_CURRENT_ASSET = "backup_current_asset"
ATTR_DOWNLOAD_FROM_REPO = "download_from_repo"
ATTR_DOWNLOAD_FROM_DEV_BRANCH = "download_from_dev_branch"
ATTR_DISCARD_DASHBOARD_USER_CHANGES = "discard_dashboard_user_changes"


VA_ATTRIBUTE_UPDATE_EVENT = "va_attr_update_event_{}"
VA_BACKGROUND_UPDATE_EVENT = "va_background_update_{}"
VA_ASSET_UPDATE_PROGRESS = "va_asset_update_progress"
VA_ADD_UPDATE_ENTITY_EVENT = "va_add_update_entity_event"
CC_CONVERSATION_ENDED_EVENT = f"{CUSTOM_CONVERSATION_DOMAIN}_conversation_ended"


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

OVERLAY_FILE_NAME = "overlay"
MIN_DASHBOARD_FOR_OVERLAYS = "1.1.0"
