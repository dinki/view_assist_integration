"""Intents for View Assist Devices to take action on the basis of fired intents."""

import logging

from homeassistant.components.conversation import ChatLog, ToolResultContent
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

        # Add intent sensor listener for vaca
        if intent_device := self._config.runtime_data.core.intent_device:
            # Add listener
            self._config.async_on_unload(
                async_track_state_change_event(
                    self._hass, intent_device, self._async_on_intent_device_change
                )
            )

        if device_id:
            self._config.async_on_unload(
                async_dispatcher_connect(
                    self._hass, f"{device_id}-intent_handled", self.async_handle_intent
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

    async def async_handle_intent(
        self, intent_obj: intent.Intent, response: intent.IntentResponse
    ) -> None:
        """Handle the intent dispatched for the device."""

        intent_type = intent_obj.intent_type
        speech_text = get_key("plain.speech", response.speech)
        sensor_entity = get_sensor_entity_from_instance(
            self._hass, self._config.entry_id
        )

        # --------------------------------------------------------------------------------
        # Extract relevant information from the intent and response
        # Set any device values here based on the intent and response
        # --------------------------------------------------------------------------------

        _LOGGER.debug(
            "Handling intent '%s' for sensor entity '%s'", intent_type, sensor_entity
        )

        payload = {
            "intent": intent_type,
            "command": intent_obj.text_input,
            "response": speech_text,
            "processed_locally": True,
            "changed_entities": [
                target.id
                for target in response.success_results
                if target.type == "entity"
            ],
            "view_data": None,
        }

        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self._config.entry_id}_event",
            VAEvent(VAEventType.INTENT_UPDATE, payload),
        )

        # Do intent navigation based on the intent type
        self.navigate_for_intent(intent_type)

    @callback
    def _async_on_intent_device_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle intent device state changes which will pick up LLM intent responses."""
        # if not self._validate_event(event):
        #    return

        new_state: State = event.data["new_state"]
        processed_locally = new_state.attributes.get("processed_locally", False)
        hass_tool_call = None

        _LOGGER.info("Received intent device change event: %s", new_state)

        if processed_locally:
            # Will have an intent trigger instead
            return

        intent_output = new_state.attributes.get("intent_output")

        if intent_output:
            if conversation_id := intent_output.get("conversation_id"):
                # Get conversation from chat log
                chatlog: ChatLog = self._hass.data.get(
                    "conversation_chat_logs", {}
                ).get(conversation_id, [])

                # Find the last user role entry created datetime
                last_user_entry_created_datetime = None
                for entry in reversed(chatlog.content):
                    if entry.role == "user":
                        last_user_entry_created_datetime = entry.created
                        break
                _LOGGER.debug(
                    "Last user entry created datetime for conversation_id '%s': %s",
                    conversation_id,
                    last_user_entry_created_datetime,
                )

                # Find if a tool result exists after the last user entry
                tool_result_after_last_user_entry = None
                for entry in chatlog.content:
                    if (
                        isinstance(entry, ToolResultContent)
                        and last_user_entry_created_datetime
                        and entry.created > last_user_entry_created_datetime
                    ):
                        tool_result_after_last_user_entry = entry
                        break

                if tool_result_after_last_user_entry:
                    hass_tool_call = tool_result_after_last_user_entry.tool_name

        if hass_tool_call:
            self.navigate_for_intent(hass_tool_call)
            return

        if intent_output:
            speech_text = get_key("response.speech.plain.speech", intent_output)
            success_results = get_key("response.data.success", intent_output)
            if speech_text:
                word_count = len(speech_text.split())
                message_font_size = ["10vw", "8vw", "6vw", "4vw"][
                    min(word_count // 6, 3)
                ]
                view_data = {
                    "title": "AI Response",
                    "message": speech_text,
                    "message_font_size": message_font_size,
                }

                payload = {
                    "intent": None,
                    "command": None,
                    "response": speech_text,
                    "processed_locally": processed_locally,
                    "changed_entities": [
                        target.get("id")
                        for target in success_results
                        if target.get("type") == "entity"
                    ],
                    "view_data": view_data,
                }

                async_dispatcher_send(
                    self._hass,
                    f"{DOMAIN}_{self._config.entry_id}_event",
                    VAEvent(VAEventType.INTENT_UPDATE, payload),
                )

                self.navigate_for_intent("info")

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
