"""View Assist websocket handlers."""

from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.components.websocket_api import (
    ActiveConnection,
    async_register_command,
    async_response,
    event_message,
    websocket_command,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)

from ..const import DOMAIN  # noqa: TID252
from ..devices.menu import MenuManager  # noqa: TID252
from ..helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_device_id_from_entity_id,
    get_entity_id_by_browser_id,
    get_mimic_entity_id,
)
from ..typed import VAConfigEntry, VAEvent, VAEventType, VAScreenMode  # noqa: TID252
from .timers import TimerManager

_LOGGER = logging.getLogger(__name__)

BROWSER_IDS = "browser_ids"
WEBSOCKET_MANAGER = "websocket_manager"
WEBSOCKET_EVENTS = [VAEventType.ASSIST_LISTENING, VAEventType.NAVIGATION]


class WebsocketManager:
    """Class to manage websocket related functionality."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> WebsocketManager | None:
        """Get the websocket manager for a config entry."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the WebsocketManager."""
        self.hass = hass
        self.config = config
        self.connections: dict[str, WebsocketListenerHandler] = {}

    async def async_setup(self) -> bool:
        """Set up the WebsocketManager."""
        # Ensure hass.data structure
        if BROWSER_IDS not in self.hass.data[DOMAIN]:
            self.hass.data[DOMAIN][BROWSER_IDS] = {}

        setup_websocket_commands(self.hass)
        return True

    async def async_unload(self) -> bool:
        """Stop the WebsocketManager."""
        for browser_id in list(self.connections.keys()):
            self.unregister_connection(browser_id, unloading=True)
        self.hass.data[DOMAIN].pop(BROWSER_IDS, None)
        return True

    async def async_register_connection(
        self, browser_id: str, connection: ActiveConnection, msg_id: int | None = None
    ):
        """Register a new connection."""

        # Add to known browser ids list
        if (
            str(browser_id).startswith("va-")
            and browser_id not in self.hass.data[DOMAIN][BROWSER_IDS]
        ):
            self.hass.data[DOMAIN][BROWSER_IDS][browser_id] = browser_id

        # Register handler for connection
        handler = WebsocketListenerHandler(self.hass, connection, browser_id, msg_id)

        # If duplicate connection, stop old one
        if browser_id in self.connections:
            self.connections[browser_id].stop()

        self.connections[browser_id] = handler
        handler.start()

    def unregister_connection(self, browser_id: str, unloading: bool = False):
        """Unregister a connection."""
        if browser_id in self.connections:
            _LOGGER.debug("Tearing down connection for %s", browser_id)
            self.connections[browser_id].stop(unloading=unloading)
            del self.connections[browser_id]

        # Remove from known browser ids list
        if browser_id in self.hass.data[DOMAIN][BROWSER_IDS]:
            del self.hass.data[DOMAIN][BROWSER_IDS][browser_id]


class WebsocketListenerHandler:
    """Class to handle websocket listeners."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection: ActiveConnection,
        browser_id: str,
        msg_id: int | None = None,
    ) -> None:
        """Initialize the WebsocketListenerHandler."""
        self.hass = hass
        self.connection = connection
        self.browser_id = browser_id
        self.msg_id = msg_id

        self.config: VAConfigEntry | None = None
        self.entity_id: str | None = None
        self.mimic: bool = False
        self.listeners: dict[str, Any] = {}

    def start(self):
        """Start listeners."""
        self.entity_id, self.mimic = self._get_entity_id(self.browser_id)
        if self.entity_id:
            self.config = get_config_entry_by_entity_id(self.hass, self.entity_id)

            if "global" not in self.listeners:
                self.listeners["global"] = async_dispatcher_connect(
                    self.hass, f"{DOMAIN}_event", self._send_event
                )
            if "device" not in self.listeners:
                self.listeners["device"] = async_dispatcher_connect(
                    self.hass,
                    f"{DOMAIN}_{self.config.entry_id}_event",
                    self._send_event,
                )
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{self.config.entry_id}_event",
                VAEvent(VAEventType.BROWSER_REGISTERED),
            )
        else:
            self._send_event(
                VAEvent(VAEventType.BROWSER_UNREGISTERED),
            )

        # Register listener for browser id changes
        if "browser_id" not in self.listeners:
            self.listeners["browser_id"] = async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self.browser_id}_event",
                self._browser_id_event,
            )

    def stop(self, unloading: bool = False):
        """Stop listeners."""
        # Tell browser to reconnect if unloading
        # Ie do not send reload event if browser disconnected
        if unloading:
            self._send_event(
                VAEvent(VAEventType.RELOAD),
            )
        for unsub_listener in self.listeners.values():
            unsub_listener()
        self.listeners = {}
        self.entity_id = None

    def _get_entity_id(self, browser_id: str) -> tuple[str | None, bool]:
        """Get entity id by browser id."""
        if entity_id := get_entity_id_by_browser_id(self.hass, browser_id):
            return entity_id, False
        if entity_id := get_mimic_entity_id(self.hass, browser_id):
            return entity_id, True
        return None, False

    @callback
    def _browser_id_event(self, event: VAEvent):
        """Handle browser id change event.

        This is for newly added devices or where a device has been re-enabled.
        """
        # Handle where device added first time or re-enabled
        _LOGGER.debug(
            "Handling browser id event %s received for %s",
            event.event_name,
            self.browser_id,
        )
        if event.event_name == VAEventType.BROWSER_REGISTERED:
            self.start()

    @callback
    def _send_event(self, event: VAEvent):
        """Send event to connection."""

        # Send timers if timer event
        if event.event_name == VAEventType.TIMER_UPDATE:
            if timers := TimerManager.get(self.hass):
                event.payload = timers.get_timers(
                    entity_id=self.entity_id, include_expired=True
                )

        # Add config data to event
        if event.event_name in [
            VAEventType.CONFIG_UPDATE,
            VAEventType.BROWSER_REGISTERED,
            VAEventType.BROWSER_UNREGISTERED,
        ]:
            event.payload = self._get_event_data()

        # Don't send reload event to mimic device
        if event.event_name == VAEventType.RELOAD and self.mimic:
            return

        # Filter event types
        if event.event_name in [
            VAEventType.BROWSER_REGISTERED,
            VAEventType.BROWSER_UNREGISTERED,
            VAEventType.CONFIG_UPDATE,
            VAEventType.ASSIST_LISTENING,
            VAEventType.NAVIGATION,
            VAEventType.TIMER_UPDATE,
            VAEventType.RELOAD,
        ]:
            _LOGGER.debug(
                "Sending event: %s to %s - %s",
                event.event_name,
                self.browser_id,
                self.entity_id if not self.mimic else f"{self.entity_id}(mimic)",
            )

            self.connection.send_message(
                event_message(
                    self.msg_id, {"event": event.event_name, "payload": event.payload}
                )
            )

    def _get_event_data(self) -> dict[str, Any]:
        output = {}
        config = self.config

        # Use mimic'd entity config if mimic device
        if self.mimic:
            config = get_config_entry_by_entity_id(self.hass, self.entity_id)

        if self.entity_id and config:
            if config.disabled_by:
                return output

            data = config.runtime_data
            timer_info = {}
            if timers := TimerManager.get(self.hass):
                timer_info = timers.get_timers(
                    entity_id=self.entity_id, include_expired=True
                )

            menu_info = {}
            if menu_manager := MenuManager.get(self.hass, config):
                menu_info["status_icons"] = (
                    menu_manager.status_icons.copy() if menu_manager else []
                )
                menu_info["menu_items"] = (
                    menu_manager.menu_items.copy() if menu_manager else []
                )
                menu_info["menu_active"] = (
                    menu_manager.active if menu_manager else False
                )
                menu_info["menu_config"] = data.dashboard.display_settings.menu_config

            try:
                output = {
                    "browser_id": self.browser_id,
                    "entity_id": self.entity_id,
                    "mimic_device": self.mimic,
                    "name": data.core.name,
                    "mic_entity_id": data.core.mic_device,
                    "mic_device_id": get_device_id_from_entity_id(
                        self.hass, data.core.mic_device
                    ),
                    "mediaplayer_entity_id": data.core.mediaplayer_device,
                    "mediaplayer_device_id": get_device_id_from_entity_id(
                        self.hass, data.core.mediaplayer_device
                    ),
                    "musicplayer_entity_id": data.core.musicplayer_device,
                    "musicplayer_device_id": get_device_id_from_entity_id(
                        self.hass, data.core.musicplayer_device
                    ),
                    "display_device_id": data.core.display_device,
                    "menu": menu_info,
                    "timers": timer_info,
                    "background": data.dashboard.background_settings.background,
                    "dashboard": data.dashboard.dashboard,
                    "home": data.dashboard.home
                    if not data.runtime_config_overrides.home
                    else data.runtime_config_overrides.home,
                    "music": data.dashboard.music,
                    "intent": data.dashboard.intent,
                    "hide_sidebar": data.dashboard.display_settings.screen_mode
                    in [
                        VAScreenMode.HIDE_HEADER_SIDEBAR,
                        VAScreenMode.HIDE_SIDEBAR,
                    ],
                    "hide_header": data.dashboard.display_settings.screen_mode
                    in [VAScreenMode.HIDE_HEADER_SIDEBAR, VAScreenMode.HIDE_HEADER],
                }
            except Exception:  # noqa: BLE001
                output = {}
        return output


def setup_websocket_commands(hass: HomeAssistant) -> None:
    """Set up websocket commands."""

    @websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/connect",
            vol.Required("browser_id"): str,
        }
    )
    @async_response
    async def handle_connect(
        hass: HomeAssistant, connection: ActiveConnection, msg: Any
    ):
        """Connect to device browser and subscribe to settings updates."""
        browser_id = msg["browser_id"]

        _LOGGER.debug("Browser with id %s connected", browser_id)

        def close_connection():
            _LOGGER.debug("Browser with id %s disconnected", browser_id)
            WebsocketManager.get(hass).unregister_connection(browser_id)

        # Register browser
        await WebsocketManager.get(hass).async_register_connection(
            browser_id, connection, msg["id"]
        )

        # Register close connection callback
        connection.subscriptions[browser_id] = close_connection

        # Send connection response
        connection.send_result(msg["id"])

    @websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/get_entity_id",
            vol.Required("browser_id"): str,
        }
    )
    @async_response
    async def handle_get_entity_by_browser_id(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict
    ) -> None:
        """Get entity id by browser id."""
        is_mimic = False
        entity_id = get_entity_id_by_browser_id(hass, msg["browser_id"])
        if not entity_id:
            if entity_id := get_mimic_entity_id(hass):
                is_mimic = True

        connection.send_result(
            msg["id"], {"entity_id": entity_id, "mimic_device": is_mimic}
        )

    # Get server datetime
    @websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/get_server_time_delta",
            vol.Required("epoch"): int,
        }
    )
    @async_response
    async def handle_get_server_time(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict
    ) -> None:
        """Get entity id by browser id."""

        delta = round(time.time() * 1000) - msg["epoch"]
        connection.send_result(msg["id"], delta)

    # Get timer by name
    @websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/get_timer",
            vol.Required("browser_id"): str,
            vol.Required("name"): str,
        }
    )
    @async_response
    async def handle_get_timer_by_name(
        hass: HomeAssistant, connection: ActiveConnection, msg: dict
    ) -> None:
        """Get entity id by browser id."""
        entity = get_entity_id_by_browser_id(hass, msg["browser_id"])
        if not entity:
            output = get_mimic_entity_id(hass)

        if entity:
            timer_name = msg["name"]
            timers = TimerManager.get(hass)

            output = timers.get_timers(device_or_entity_id=entity, name=timer_name)

        connection.send_result(msg["id"], output)

    # Register commands
    async_register_command(hass, handle_connect)
    async_register_command(hass, handle_get_entity_by_browser_id)
    async_register_command(hass, handle_get_server_time)
    async_register_command(hass, handle_get_timer_by_name)
