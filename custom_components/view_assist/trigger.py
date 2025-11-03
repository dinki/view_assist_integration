"""View Assist trigger dispatcher."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_COMMAND, CONF_OPTIONS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.intent import Intent
from homeassistant.helpers.script import UNDEFINED, ScriptRunResult
from homeassistant.helpers.trigger import Trigger, TriggerActionRunner, TriggerConfig
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .core.intents import IntentsManager, IntentTriggerDetails, IntentTriggerResponse

_LOGGER = logging.getLogger(__name__)

CONF_INTENT = "intent"

_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_INTENT): cv.string,
        vol.Optional(CONF_COMMAND): cv.string,
    }
)

_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_OPTIONS): _OPTIONS_SCHEMA,
    }
)


class VAIntentTrigger(Trigger):
    """Trigger on events."""

    _options: dict[str, Any]

    @classmethod
    async def async_validate_config(
        cls, hass: HomeAssistant, config: ConfigType
    ) -> ConfigType:
        """Validate trigger-specific config."""
        return _CONFIG_SCHEMA(config)

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Initialize trigger."""
        super().__init__(hass, config)
        assert config.options is not None
        self._options = config.options
        self._unregister: CALLBACK_TYPE | None = None

    async def async_attach_runner(
        self, run_action: TriggerActionRunner
    ) -> CALLBACK_TYPE:
        """Attach the trigger."""

        @callback
        def async_remove() -> None:
            """Remove trigger."""
            _LOGGER.warning(
                "Unregistering intent trigger -> %s - %s",
                self._options[CONF_INTENT],
                self._options.get(CONF_COMMAND),
            )
            if self._unregister:
                self._unregister()

        async def async_on_event(
            intent_obj: Intent, extra_data: dict[str, Any] | None = None
        ) -> IntentTriggerResponse | None:
            """Handle event."""

            satellite_id = intent_obj.satellite_id
            device_id = intent_obj.device_id

            trigger_input: dict[str, Any] = {  # Satisfy type checker
                "platform": DOMAIN,
                "sentence": intent_obj.text_input,
                "slots": {  # direct access to values
                    entity_name: entity["value"]
                    for entity_name, entity in intent_obj.slots.items()
                },
                "device_id": device_id,
                "satellite_id": satellite_id,
            }

            if extra_data:
                trigger_input.update(extra_data)

            _LOGGER.debug("Running automation for intent trigger: %s", trigger_input)

            automation_result = await run_action(
                trigger_input,
                f"Intent Trigger for {intent_obj.intent_type}",
            )

            _LOGGER.debug("Automation result: %s", automation_result)
            response = IntentTriggerResponse()

            if isinstance(automation_result, ScriptRunResult):
                if automation_result.conversation_response not in (None, UNDEFINED):
                    response.conversation_response = (
                        automation_result.conversation_response
                    )  # type: ignore[return-value]

                # Add variables as response slots
                if automation_result.variables not in (None, UNDEFINED):
                    for key, value in automation_result.variables.items():
                        if key not in ("this", "trigger", "context"):
                            if response.response_slots is None:
                                response.response_slots = {}
                            response.response_slots[key] = value
                return response

            # It's important to return None here instead of a string.
            #
            # When editing in the UI, a copy of this trigger is registered.
            # If we return a string from here, there is a race condition between the
            # two trigger copies for who will provide a response.
            return None

        # Register event listener
        _LOGGER.warning("Registering intent trigger")
        if im := IntentsManager.get(self._hass):
            command = self._options.get(CONF_COMMAND, "")
            _LOGGER.warning(
                "Registering intent trigger for intent %s with command: %s",
                self._options[CONF_INTENT],
                command,
            )
            self._unregister = im.register_trigger(
                IntentTriggerDetails(
                    intent=self._options[CONF_INTENT],
                    command=command,
                    callback=async_on_event,
                )
            )

        return async_remove


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return triggers provided by this integration."""
    _LOGGER.debug("Registering triggers for View Assist")
    return {
        "intent_trigger": VAIntentTrigger,
    }
