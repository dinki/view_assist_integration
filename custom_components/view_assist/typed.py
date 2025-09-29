"""Types for View Assist."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry

type VAConfigEntry = ConfigEntry[MasterConfigRuntimeData | DeviceRuntimeData]


class VAType(StrEnum):
    """Sensor type enum."""

    MASTER_CONFIG = "master_config"
    VIEW_AUDIO = "view_audio"
    AUDIO_ONLY = "audio_only"


class VATimeFormat(StrEnum):
    """Time format enum."""

    HOUR_12 = "hour_12"
    HOUR_24 = "hour_24"


class VAScreenMode(StrEnum):
    """Screen mode enum."""

    NO_HIDE = "no_hide"
    HIDE_HEADER = "hide_header"
    HIDE_SIDEBAR = "hide_sidebar"
    HIDE_HEADER_SIDEBAR = "hide_header_sidebar"


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


class VABackgroundMode(StrEnum):
    """Background mode enum."""

    DEFAULT_BACKGROUND = "default_background"
    LOCAL_SEQUENCE = "local_sequence"
    LOCAL_RANDOM = "local_random"
    DOWNLOAD_RANDOM = "download"
    LINKED = "link_to_entity"


class VAMenuConfig(StrEnum):
    """Menu configuration options enum."""

    DISABLED = "menu_disabled"
    ENABLED_VISIBLE = "menu_enabled_button_visible"
    ENABLED_HIDDEN = "menu_enabled_button_hidden"


@dataclass
class IntegrationConfig:
    """Class to hold integration config data."""

    enable_updates: bool = True


@dataclass
class DeviceCoreConfig:
    """Class to hold core config data."""

    type: VAType | None = None
    name: str | None = None
    mic_device: str | None = None
    mediaplayer_device: str | None = None
    musicplayer_device: str | None = None
    intent_device: str | None = None
    display_device: str | None = None
    dev_mimic: bool | None = None


@dataclass
class BackgroundConfig:
    "Background settings class."

    background_mode: str | None = None
    background: str | None = None
    rotate_background_path: str | None = None
    rotate_background_linked_entity: str | None = None
    rotate_background_interval: int | None = None


@dataclass
class DisplayConfig:
    """Display settings class."""

    assist_prompt: str | None = None
    avatar_choice: str = "jarvis"
    status_icons_size: VAIconSizes | None = None
    font_style: str | None = None
    status_icons: list[str] = field(default_factory=list)
    menu_config: VAMenuConfig = VAMenuConfig.DISABLED
    menu_items: list[str] = field(default_factory=list)
    menu_timeout: int = 10
    time_format: VATimeFormat | None = None
    screen_mode: VAScreenMode | None = None


@dataclass
class DashboardConfig:
    """Class to hold dashboard config data."""

    dashboard: str | None = None
    home: str | None = None
    music: str | None = None
    intent: str | None = None
    list_view: str | None = None
    background_settings: BackgroundConfig = field(default_factory=BackgroundConfig)
    display_settings: DisplayConfig = field(default_factory=DisplayConfig)


@dataclass
class DefaultConfig:
    """Class to hold default config data."""

    weather_entity: str | None = None
    mode: str | None = None
    view_timeout: int | None = None
    do_not_disturb: bool | None = None
    use_announce: bool | None = None
    mic_unmute: bool | None = None
    ducking_volume: int | None = None


@dataclass
class DeveloperConfig:
    """Class to hold developer config data."""

    developer_device: str | None = None
    developer_mimic_device: str | None = None


class MasterConfigRuntimeData:
    """Class to hold master config data."""

    def __init__(self) -> None:
        """Initialize runtime data."""
        self.integration: IntegrationConfig = IntegrationConfig()
        self.dashboard: DashboardConfig = DashboardConfig()
        self.default: DefaultConfig = DefaultConfig()
        self.developer_settings: DeveloperConfig = DeveloperConfig()
        # Extra data for holding key/value pairs passed in by set_state service call
        self.extra_data: dict[str, Any] = {}


class DeviceRuntimeData:
    """Class to hold runtime data."""

    def __init__(self) -> None:
        """Initialize runtime data."""
        self.core: DeviceCoreConfig = DeviceCoreConfig()
        self.dashboard: DashboardConfig = DashboardConfig()
        self.default: DefaultConfig = DefaultConfig()
        # Extra data for holding key/value pairs passed in by set_state service call
        self.extra_data: dict[str, Any] = {}


@dataclass
class VAEvent:
    """View Assist event."""

    event_name: str
    payload: dict | None = None
