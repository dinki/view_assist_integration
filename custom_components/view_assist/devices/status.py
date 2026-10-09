"""Device Status manager.

All accessible attributs should be added to the Status class.
Any new attributes added to the status should be included in PERSISTED_ATTRIBUTES if they need to be persisted.
An _on_set_[attr_name] method will be called whenever the corresponding attribute is set, allowing for custom handling of attribute changes.
Returning False from an _on_set_[attr_name] method will prevent the attribute from being updated, which is useful for validation or conditional updates.
Setting the hold value when mode is set to hold instead of setting mode to hold uses this method.

"""

import contextlib  # noqa: I001
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.assist_satellite.entity import AssistSatelliteState
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import DEVICES, DOMAIN, MUSIC_MEDIA_TYPES, VAMode  # noqa: TID252
from ..core.timers import Timer, TimerEvent, TimerManager  # noqa: TID252
from ..helpers import (  # noqa: TID252
    get_device_id_from_entity_id,
    get_sensor_entity_from_instance,
)
from ..core.conversation_monitor import ConversationMonitor  # noqa: TID252
from ..typed import (  # noqa: TID252
    DashboardConfig,
    DefaultConfig,
    DeviceCoreConfig,
    VAConfigEntry,
    VAEvent,
    VAEventType,
    VAScreenMode,
)
from .base import DeviceModule
from .menu import MenuManager
from .navigation import NavigationManager

_LOGGER = logging.getLogger(__name__)

# Time to wait for further attribute changes before sending a status update,
# to batch rapid successive changes into a single notification.
NOTIFY_DEBOUNCE_SECONDS = 0.2

PERSISTED_ATTRIBUTES = [
    "muted",
    "mode",
    "do_not_disturb",
    "extra_data",
]


@dataclass
class Config:
    """Class to represent the configuration of a device."""

    core: DeviceCoreConfig | None = None
    dashboard: DashboardConfig | None = None
    default: DefaultConfig | None = None


@dataclass
class Status:
    """Class to represent the status of a device."""

    config: Config | None = None
    browser_connected: bool = False
    assist_state: AssistSatelliteState = AssistSatelliteState.IDLE
    muted: bool = False
    current_path: str | None = None
    hold: bool = False
    mode: VAMode = VAMode.NORMAL
    is_music_playing: bool = False
    media_content_type: str | None = None
    media_artist: str | None = None
    media_album: str | None = None
    media_track: str | None = None
    hold_view: str | None = None
    background: str | None = None
    last_intent: str | None = None
    status_icons: dict[str, Any] | None = None
    menu_items: dict[str, Any] | None = None
    menu_active: bool = False
    last_command: str | None = None
    last_response: str | None = None
    changed_entities: list[str] | None = None
    view_data: dict[str, Any] | None = None
    hide_sidebar: bool = False
    hide_header: bool = False

    volume: int | None = None
    do_not_disturb: bool = False
    has_timers: bool = False
    timers: list[Timer] | None = None
    alarm_sounding: bool = False
    last_activity: datetime | None = None
    extra_data: dict[str, Any] | None = None


class StatusStore:
    """Class to store persistent status data."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self._hass = hass
        self._config = config
        self.store = Store(hass, 1, f"{DOMAIN}.{self._config.entry_id}")

    async def async_load(self) -> dict[str, Any] | None:
        """Load the stored status."""
        return await self.store.async_load()

    async def async_save(self, status: Status) -> None:
        """Update the stored status."""
        persist_data = {"last_updated": dt_util.now()}

        for attr in PERSISTED_ATTRIBUTES:
            if hasattr(status, attr):
                persist_data[attr] = getattr(status, attr, None)

        await self.store.async_save(persist_data)


class StatusManager(DeviceModule, Status):
    """Class to manage device status."""

    @classmethod
    def get(cls, hass: HomeAssistant, config: VAConfigEntry) -> StatusManager | None:
        """Get the instance for a config entry."""
        try:
            return hass.data[DOMAIN][DEVICES][config.entry_id][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the status manager."""
        super().__init__(hass, config)
        self._store = StatusStore(hass, config)
        self._entity_id = "Unknown"
        self._rt = config.runtime_data
        self._notify_cancel: CALLBACK_TYPE | None = None
        self._event_listeners: dict[str, list[CALLBACK_TYPE]] = {}
        self.force_activity_timeout_flag: bool = False

        # Device entry events
        self._config.async_on_unload(
            async_dispatcher_connect(
                self._hass,
                f"{DOMAIN}_{self._config.entry_id}_event",
                self._async_event_handler,
            )
        )

    def __setattr__(self, name: str, value: Any) -> None:
        """Set an attribute, notifying on_change if the value changed."""
        cancel = False
        if not name.startswith("_") and name not in [
            "force_activity_timeout_flag",
            "last_activity",
        ]:
            if self._initialised and self._on_change:
                # Only set to false if false.  If True or None then set True
                if self._on_change(name, value) == False:  # noqa: E712
                    cancel = True
        if not cancel:
            super().__setattr__(name, value)

    async def async_setup(self) -> bool:
        """Set up the status manager."""
        self._entity_id = get_sensor_entity_from_instance(
            self._hass, self._config.entry_id
        )
        self.config = Config(
            core=self._rt.core,
            dashboard=(self._rt.dashboard if hasattr(self._rt, "dashboard") else None),
            default=(self._rt.default if hasattr(self._rt, "default") else None),
        )

        # Load persisted status from the store
        await self._async_load_from_store()

        if self.extra_data is None:
            self.extra_data = {}

        # Set status from defaults
        self.mode = self._rt.default.mode
        self.do_not_disturb = self._rt.default.do_not_disturb

        # Get current timers
        if tm := TimerManager.get(self._hass):
            self.timers = tm.get_timers_as_dict(entity_id=self._entity_id)
        self.has_timers = len(self.timers) > 0

        # Set sidebar and header bar status
        self.hide_sidebar = self.config.dashboard.display_settings.screen_mode in [
            VAScreenMode.HIDE_HEADER_SIDEBAR,
            VAScreenMode.HIDE_SIDEBAR,
        ]
        self.hide_header = self.config.dashboard.display_settings.screen_mode in [
            VAScreenMode.HIDE_HEADER_SIDEBAR,
            VAScreenMode.HIDE_HEADER,
        ]
        self.current_path = self.config.dashboard.home

        self.last_activity = dt_util.now()

        return True

    def register_activity(self) -> None:
        """Register the last activity for the device."""
        self.last_activity = dt_util.now()

    def force_activity_timeout(self, delay: int = 1) -> None:
        """Force an activity timeout for the device."""

        def _set_force_activity_timeout_flag() -> None:
            self.force_activity_timeout_flag = True

        if delay > 0:
            self._hass.loop.call_later(delay, _set_force_activity_timeout_flag)
        else:
            self.force_activity_timeout_flag = True

    async def _async_load_from_store(self) -> None:
        """Load the status from the store."""
        persist_data = await self._store.async_load()
        if persist_data:
            for attr in PERSISTED_ATTRIBUTES:
                if attr in persist_data:
                    setattr(self, attr, persist_data[attr])

    def as_dict(self) -> dict[str, Any]:
        """Return the status as a dictionary."""
        output = {
            attr: getattr(self, attr)
            for attr in Status.__dict__
            if not attr.startswith("_")
        }
        with contextlib.suppress(Exception):
            output["config"]["core"]["mic_device_id"] = get_device_id_from_entity_id(
                self.hass, self.config.core.mic_device
            )
            output["config"]["core"]["mediaplayer_device_id"] = (
                get_device_id_from_entity_id(
                    self.hass, self.config.core.mediaplayer_device
                )
            )
            output["config"]["core"]["musicplayer_device_id"] = (
                get_device_id_from_entity_id(
                    self.hass, self.config.core.musicplayer_device
                )
            )
        return output

    def _on_change(self, name: str, value: Any) -> bool | None:
        """Handle changes to the status attributes."""
        result = None
        if hasattr(self, f"_on_set_{name}"):
            result = getattr(self, f"_on_set_{name}")(value)
        self._schedule_notify_status_update()
        return result

    def _schedule_notify_status_update(self) -> None:
        """Schedule a debounced status update notification.

        Batches rapid successive attribute changes into a single notification.
        Safe to call from any thread; the actual scheduling always runs on
        the event loop.
        """

        if self._notify_cancel:
            self._notify_cancel()

        @callback
        def _notify(_now: datetime) -> None:
            self._notify_cancel = None
            async_dispatcher_send(
                self._hass,
                f"{DOMAIN}_{self._config.entry_id}_event",
                VAEvent(VAEventType.STATUS_CHANGE),
            )
            self._hass.async_create_background_task(
                self._store.async_save(self),
                name=f"save_status_{self._config.entry_id}",
            )

        self._notify_cancel = async_call_later(
            self._hass, NOTIFY_DEBOUNCE_SECONDS, _notify
        )

    async def _async_event_handler(self, event: VAEvent) -> None:  # noqa: C901
        """Handle events dispatched to this status manager."""
        event_type = event.event_name

        # Handle browser registration events
        if event_type == VAEventType.BROWSER_REGISTERED:
            browser_id = event.payload.get("browser_id", None)
            self.browser_connected = bool(browser_id)
            # Force correct screen by forcing activity timeout
            self.force_activity_timeout()

        # Handle browser unregistration events
        elif event_type == VAEventType.BROWSER_UNREGISTERED:
            self.browser_connected = False

        # Handle assist state update events
        elif event_type == VAEventType.ASSIST_STATE_CHANGE:
            # Register activity first to prevent immediate timeout
            self.register_activity()
            self.assist_state = event.payload.get("state", AssistSatelliteState.IDLE)

            # Register state with chat log monitor to support last conversation statuses
            if clm := ConversationMonitor.get(self._hass):
                clm.register_assist_status(self._config.entry_id, self.assist_state)

        # Handle background image change events
        elif event_type == VAEventType.BACKGROUND_CHANGE:
            self.background = event.payload.get("background", "")

        # Handle menu manager updates
        elif event_type == VAEventType.ICONS_UPDATE:
            self.status_icons = event.payload.get("status_icons", {})
            self.menu_items = event.payload.get("menu_items", {})
            self.menu_active = event.payload.get("menu_active", False)

        # Handle timer update
        elif event_type == VAEventType.TIMER_UPDATE:
            timer_event = event.payload.get("event", None)
            if tm := TimerManager.get(self._hass):
                self.timers = tm.get_timers_as_dict(entity_id=self._entity_id)
                self.has_timers = len(self.timers) > 0
            self.register_activity()
            _LOGGER.debug(
                "Timers event for device %s: %s",
                self._config.runtime_data.core.name,
                event.payload,
            )
            # If warning or expiry, show timer page
            if timer_event in (TimerEvent.WARNING, TimerEvent.EXPIRED):
                if nm := NavigationManager.get(self._hass, self._config):
                    nm.browser_navigate(self._config.runtime_data.dashboard.timers)

        # Handle alarm sounding events
        elif event_type == VAEventType.ALARM_SOUNDING:
            self.alarm_sounding = event.payload.get("state", False)
            if nm := NavigationManager.get(self._hass, self._config):
                nm.browser_navigate(self._config.runtime_data.dashboard.timers)

        # Handle music player updates
        elif event_type == VAEventType.MUSIC_PLAYER_STATE_CHANGE:
            payload = event.payload
            state = payload.get("state", MediaPlayerState.IDLE)
            attrs = payload.get("attributes", {})

            state_changed = self.is_music_playing != (state == MediaPlayerState.PLAYING)

            if state_changed:
                self.is_music_playing = state == MediaPlayerState.PLAYING
                self.media_content_type = attrs.get("media_content_type")
                self.media_artist = attrs.get("media_artist")
                self.media_album = attrs.get("media_album_name")
                self.media_track = attrs.get("media_title")

                if (
                    self.is_music_playing
                    and self.media_content_type in MUSIC_MEDIA_TYPES
                    and self.current_path != self._config.runtime_data.dashboard.music
                ):
                    self.register_activity()
                    if nm := NavigationManager.get(self._hass, self._config):
                        nm.browser_navigate(self._config.runtime_data.dashboard.music)

                # Force revert without waiting for the normal timeout
                if not self.is_music_playing:
                    self.force_activity_timeout()

        # Handle mic mute switch update
        elif event_type == VAEventType.MICROPHONE_STATE_CHANGE:
            self.muted = event.payload.get("state") == STATE_ON
            # Use menu manager to update status icons
            if menu_manager := MenuManager.get(self._hass, self._config):
                if self.muted:
                    menu_manager.add_items(items=["mic"], menu=False)
                else:
                    menu_manager.remove_items(items=["mic"], menu=False)

        # Handle intent updates
        elif event_type == VAEventType.INTENT_UPDATE:
            # Process intent update payload
            payload = event.payload
            if "intent" in payload:
                self.last_intent = payload.get("intent")
            if "command" in payload:
                self.last_command = payload.get("command")
            if "response" in payload:
                self.last_response = payload.get("response")
            if "changed_entities" in payload:
                self.changed_entities = payload.get("changed_entities", [])
            if "view_data" in payload:
                self.view_data = payload.get("view_data")

            self.register_activity()

        # Handle view change update from websocket
        elif event_type == VAEventType.VIEW_UPDATE:
            self.current_path = event.payload.get("path")
            self.register_activity()

        # Handle screen touch activity from websocket
        elif event_type == VAEventType.SCREEN_ACTIVITY:
            _LOGGER.debug(
                "Screen activity detected for device %s ",
                self._config.runtime_data.core.name,
            )
            self.register_activity()

    async def handle_set_state_action_call(self, data: dict[str, Any]) -> bool:
        """Handle a set state action call for the device."""
        # Implement the logic to handle the set state action call here
        # Return True if the action was successfully handled, False otherwise
        for name, value in data.items():
            if hasattr(self, name):
                _LOGGER.debug("Setting %s to %s for %s", name, value, self._entity_id)
                setattr(self, name, value)
                continue

            _LOGGER.debug("Handling extra data for %s: %s", name, value)
            if value:
                self.extra_data[name] = value
            else:
                self.extra_data.pop(name, None)

    def _on_set_mode(self, value: str) -> bool:
        """Set the current mode of the device."""
        # Seperation of hold from mode
        if value == "hold":
            self.hold = True
            return False

        if value == "normal":
            self.hold = False
        return True

    def _on_set_hold(self, enable: bool) -> None:
        """Set the hold status for the device."""
        if self.hold != enable:
            self.hold_view = self.current_path if enable else None
            if mm := MenuManager.get(self._hass, self._config):
                if enable:
                    mm.add_items(VAMode.HOLD)
                else:
                    mm.remove_items(VAMode.HOLD)

        # Force activity timeout if hold is disabled
        if not self.hold:
            self.force_activity_timeout()

    def _on_set_current_path(self, path: str) -> None:
        """Set the current path for the device."""
        _LOGGER.debug(
            "Setting current path for device %s to %s",
            self._config.runtime_data.core.name,
            path,
        )

    def _on_set_do_not_disturb(self, enable: bool) -> None:
        """Set the Do Not Disturb icon for the device."""
        if mm := MenuManager.get(self._hass, self._config):
            if enable:
                mm.add_items(VAMode.DND)
            else:
                mm.remove_items(VAMode.DND)
