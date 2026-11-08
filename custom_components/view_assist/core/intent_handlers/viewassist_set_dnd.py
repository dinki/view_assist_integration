"""Assist Satellite intents."""

import logging
from typing import override

import voluptuous as vol

from homeassistant.helpers import intent

from ...const import DOMAIN  # noqa: TID252
from . import IntentDeviceInfo

_LOGGER = logging.getLogger(__name__)

# Spoken values mapped to the stored "on"/"off" value
DND_VALUES = {
    "on": "on",
    "enable": "on",
    "enabled": "on",
    "off": "off",
    "disable": "off",
    "disabled": "off",
}


class VASetDNDIntentHandler(intent.IntentHandler):
    """Change View Asist do not disturb setting."""

    intent_type = "ViewAssistSetDND"
    description = """
        Changes setting for do not disturb on a View Assist satellite through voice command.
    """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("dnd_setting"): str,
        }

    @override
    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
        """Change View Assist do not disturb setting."""
        hass = intent_obj.hass
        raw_value = str(intent_obj.slots["dnd_setting"]["value"]).strip().lower()
        dnd_setting = DND_VALUES.get(raw_value)
        response = intent_obj.create_response()

        if dnd_setting is None:
            _LOGGER.warning("Unknown do not disturb value '%s'", raw_value)
            response.async_set_speech(
                f"I didn't understand the do not disturb setting {raw_value}."
            )
            return response

        # extra_data is empty on the voice path, so take the satellite
        # from the device that received the command.
        entity_id = IntentDeviceInfo(intent_obj).entity_id
        if entity_id is None:
            _LOGGER.warning("No View Assist satellite found for do not disturb")
            response.async_set_speech(
                "I couldn't tell which satellite to change."
            )
            return response

        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {"do_not_disturb": dnd_setting},
            blocking=True,
            context=intent_obj.context,
            target={"entity_id": entity_id},
        )

        response.async_set_speech_slots({"dnd_setting": dnd_setting})
        return response
