"""Manage VA intents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.intent import (
    DATA_KEY as INTENT_DATA_KEY,
    Intent,
    IntentHandler,
    IntentResponse,
    async_register,
    async_remove,
)
from homeassistant.helpers.start import async_at_started

from ..const import DOMAIN  # noqa: TID252
from ..helpers import get_entity_id_from_conversation_device_id  # noqa: TID252
from ..typed import VAConfigEntry  # noqa: TID252

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class IntentTriggerResponse:
    """Response from an intent trigger."""

    conversation_response: str | None = None
    response_slots: dict[str, Any] | None = None


@dataclass(slots=True)
class IntentTriggerDetails:
    """List of commands and the callback for a trigger."""

    intents: list[str]
    commands: list[str] | None
    callback: Callable[[Any, Any], Awaitable[IntentTriggerResponse | None]]


class IntentsManager:
    """Manage VA intents."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> IntentsManager | None:
        """Get the intents manager for a config entry."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.triggers: list[IntentTriggerDetails] = []

    async def async_setup(self) -> bool:
        """Set up the Intents Manager."""
        async_register(self.hass, VAIntentHandler())
        # Hook into intent handlers after Home Assistant has started
        # and hopefully all integrations have registered their intents
        async_at_started(self.hass, self.register_intent_hooks)

        return True

    async def async_unload(self) -> bool:
        """Unload the Intents Manager."""
        # Currently nothing to unload
        self.unregister_intent_hooks()
        async_remove(self.hass, VAIntentHandler.intent_type)
        return True

    @callback
    def register_intent_hooks(self, hass: HomeAssistant) -> None:
        """Get registered intents."""
        all_intents = hass.data.get(INTENT_DATA_KEY, {}).copy()
        for intent_type, handler in all_intents.items():
            if not isinstance(handler, IntentHookHandler):
                _LOGGER.debug("Registered intent: %s -> %s", intent_type, handler)
                async_remove(hass, intent_type)
                async_register(
                    hass,
                    IntentHookHandler(
                        real_handler=handler,
                    ),
                )

    def unregister_intent_hooks(self):
        """Unregister intent hooks and restore original handlers."""
        all_intents = self.hass.data.get(INTENT_DATA_KEY, {}).copy()
        for intent_type, handler in all_intents.items():
            if isinstance(handler, IntentHookHandler) and handler.real_handler:
                _LOGGER.debug("Restoring original intent handler for: %s", intent_type)
                async_remove(self.hass, intent_type)
                async_register(self.hass, handler.real_handler)

    def register_trigger(self, trigger: IntentTriggerDetails) -> Callable:
        """Register a trigger."""
        self.triggers.append(trigger)

        @callback
        def unregister_trigger() -> None:
            """Unregister the trigger."""
            self.triggers.remove(trigger)

        return unregister_trigger

    async def async_process_triggers(
        self, intent_obj: Intent
    ) -> IntentTriggerResponse | None:
        """Process triggers for a given intent."""
        result = None
        if intent_obj.slots and "command" in intent_obj.slots:
            _LOGGER.debug(
                "Looking for trigger: %s -> %s",
                intent_obj.intent_type,
                intent_obj.slots["command"]["value"],
            )
        else:
            _LOGGER.debug("Looking for trigger: %s", intent_obj.intent_type)

        for trigger in self.triggers:
            _LOGGER.debug("Checking trigger: %s", trigger)
            if intent_obj.intent_type in trigger.intents:
                command_match = not trigger.commands or (
                    intent_obj.slots
                    and "command" in intent_obj.slots
                    and intent_obj.slots["command"]["value"] in trigger.commands
                )
                if command_match:
                    _LOGGER.debug(
                        "Processing triggers for intent: %s with commands: %s",
                        intent_obj.intent_type,
                        trigger.commands,
                    )
                    extra_data = await self.get_trigger_extra_data(intent_obj)
                    response = await trigger.callback(intent_obj, extra_data)
                    _LOGGER.debug("Trigger result: %s", response)
                    if response:
                        result = response
        return result

    async def get_trigger_extra_data(self, intent_obj: Intent) -> dict[str, Any]:
        """Get extra data for the intent."""
        display = (
            get_entity_id_from_conversation_device_id(self.hass, intent_obj.device_id)
            if intent_obj.device_id
            else None
        )
        return {"display": display}


class VAIntentHandler(IntentHandler):
    """Intent handler for VA intents."""

    intent_type = "VACustomIntent"
    description = "Handles custom View Assist intents"

    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Handle the intent."""
        _LOGGER.debug("VAIntentHandler invoked with intent: %s", intent_obj.slots)
        response = intent_obj.create_response()

        # TODO: Add custom handling logic here with before/after hooks, pre-defined slots, etc.

        _LOGGER.debug("Handling VACustomIntent intent: %s", response.as_dict())
        return response


class IntentHookHandler(IntentHandler):
    """Base class for intent handlers with custom hooks."""

    def __init__(
        self,
        real_handler: IntentHandler,
    ) -> None:
        """Initialize the intent handler."""
        self.intent_type = real_handler.intent_type
        self.platforms = real_handler.platforms
        self.description = (
            real_handler.description or f"Hooked handler for {real_handler.intent_type}"
        )
        self.real_handler = real_handler

    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Handle the intent with custom logic."""
        _LOGGER.debug(
            "%s invoked with intent: %s -> %s",
            self.__class__.__name__,
            intent_obj.intent_type,
            intent_obj.slots,
        )
        # Custom handling logic can be added here
        if im := IntentsManager.get(intent_obj.hass):
            result = await im.async_process_triggers(intent_obj)

        # Call original handler
        response = await self.real_handler.async_handle(intent_obj)
        if result:
            if result.conversation_response:
                response.async_set_speech(result.conversation_response)
            if result.response_slots:
                response.async_set_speech_slots(result.response_slots)

        _LOGGER.debug("%s response: %s", self.__class__.__name__, response.as_dict())
        return response
