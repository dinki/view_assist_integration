"""Assist Satellite intents."""

from typing import override

import voluptuous as vol

from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.intent import Intent, IntentResponse

from ...const import DOMAIN, VAMode  # noqa: TID252
from . import IntentOverrideHandler

from ...typed import (  # noqa: TID252
    VAEvent,
    VAEventType,
)


class VASetModeIntentHandler(IntentOverrideHandler):
    """Change View Asist Modes."""

    intent_type = "ViewAssistSetMode"
    description = """
        Change modes on a View Assist satellite through voice command.
    """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("mode"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Change View Assist mode."""
        hass = intent_obj.hass
        mode = intent_obj.slots["mode"]["value"]
        device_info = self.get_device_info(intent_obj)

        # Enumerate VAMode values to validate the mode
        valid_mode = False
        for mode_item in VAMode:
            if mode == mode_item.value:
                valid_mode = True
                break

        if not valid_mode:
            raise ValueError(f"Invalid mode: {mode}")

        # Send mode set event
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_{device_info.entry_id}_event",
            VAEvent(VAEventType.MODE_CHANGE, {"mode": mode}),
        )

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {
                "mode": intent_obj.slots["mode"]["value"],
            }
        )
        return response
