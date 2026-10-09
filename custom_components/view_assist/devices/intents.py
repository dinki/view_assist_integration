"""Intents for View Assist Devices to take action on the basis of fired intents."""

import logging
from typing import Any

from homeassistant.components.conversation import ChatLog, ToolResultContent
from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import intent
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_state_change_event

from ..const import DEVICES, DOMAIN, INTENT_TO_VIEW_MAPPING  # noqa: TID252
from ..helpers import (  # noqa: TID252
    get_device_id_from_entity_id,
    get_key,
    get_sensor_entity_from_instance,
)
from ..typed import VAConfigEntry, VAEvent, VAEventType  # noqa: TID252
from .base import DeviceModule
from .navigation import NavigationManager

_LOGGER = logging.getLogger(__name__)


class DeviceIntentsHandler(DeviceModule):
    """Class to handle intents for View Assist Devices."""

    _dependencies = ["StatusManager"]

    @classmethod
    def get(
        cls, hass: HomeAssistant, config: VAConfigEntry
    ) -> DeviceIntentsHandler | None:
        """Get the instance for a config entry."""
        try:
            return hass.data[DOMAIN][DEVICES][config.entry_id][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize device intents handler."""
        super().__init__(hass, config)

        self.current_conversation_id: str | None = None

        self.register_listener()

    def register_listener(self) -> None:
        """Register event listeners for intents."""
        device_id = get_device_id_from_entity_id(
            self._hass, self._config.runtime_data.core.mic_device
        )
        device_id = None

        if device_id:
            self._config.async_on_unload(
                async_dispatcher_connect(
                    self._hass,
                    f"{device_id}_intent_event",
                    self.async_handle_intent_event,
                )
            )

    def _validate_event(self, event: Event[EventStateChangedData]) -> bool:
        """Validate event."""
        if not event.data.get("new_state"):
            # If not new state some weird error, so ignore it
            return False
        if not event.data.get("old_state"):
            # Initial state has no old state, so always process it
            return True
        if (
            event.data.get("old_state")
            and event.data["old_state"].state == event.data["new_state"].state
        ):
            # If not change to state, ignore
            return False
        return True

    async def async_handle_chatlog_event(
        self,
        chatlog: dict[str, Any],
    ) -> None:
        """Handle chat log events for the device."""
        _LOGGER.debug(
            "Handling chat log event for chatlog: %s",
            chatlog,
        )

    async def async_handle_intent_event(
        self,
        intent_obj: intent.Intent,
        response: intent.IntentResponse | None = None,
        view_data: dict[str, Any] | None = None,
    ) -> None:
        """Handle the intent dispatched for the device."""

        intent_type = intent_obj.intent_type
        sensor_entity = get_sensor_entity_from_instance(
            self._hass, self._config.entry_id
        )

        # --------------------------------------------------------------------------------
        # Extract relevant information from the intent and response
        # Set any device values here based on the intent and response
        # --------------------------------------------------------------------------------

        _LOGGER.debug(
            "Handling intent '%s' for sensor entity '%s' -> response '%s'",
            intent_type,
            sensor_entity,
            response,
        )

        payload = {
            "intent": intent_type,
            "command": intent_obj.text_input,
            "processed_locally": True,
        }

        if response is not None:
            payload["changed_entities"] = (
                [
                    target.id
                    for target in response.success_results
                    if target.type == "entity"
                ],
            )

        if view_data is not None:
            payload["view_data"] = view_data

        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self._config.entry_id}_event",
            VAEvent(VAEventType.INTENT_UPDATE, payload),
        )

        # Do intent navigation based on the intent type
        self.navigate_for_intent(intent_type)

    def navigate_for_intent(self, intent_type: str) -> None:
        """Navigate to the appropriate view for the given intent type."""
        view_matches = [
            view
            for view, intents in INTENT_TO_VIEW_MAPPING.items()
            if intent_type in intents
        ]

        if not view_matches:
            _LOGGER.debug("No view mapping found for intent: %s", intent_type)
            return

        view = view_matches[0]
        dashboard = self._config.runtime_data.dashboard.dashboard

        if hasattr(self._config.runtime_data.dashboard, view):
            path = getattr(self._config.runtime_data.dashboard, view)
        else:
            # Navigation will fallback to using view as view name if config item not found
            path = view

        if dashboard and not path.startswith(dashboard):
            path = f"{dashboard}/{path}"
        if not path.startswith("/"):
            path = f"/{path}"

        nm = NavigationManager.get(self._hass, self._config)
        nm.browser_navigate(path=path)
