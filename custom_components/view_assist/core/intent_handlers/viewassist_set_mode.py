"""Assist Satellite intents."""

from typing import override

import voluptuous as vol

from homeassistant.helpers import intent

from ...const import DOMAIN  # noqa: TID252


class VASetModeIntentHandler(intent.IntentHandler):
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
    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
        """Change View Assist mode."""
        hass = intent_obj.hass
        mode = intent_obj.slots["mode"]["value"]
        entity_id = (
            extra_data["entity_id"]
            if extra_data and "entity_id" in extra_data
            else None
        )

        # Call mode change here
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {"data": {"mode": mode}},
            blocking=True,
            context=intent_obj.context,
            target={"entity_id": entity_id},
        )

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {
                "mode": intent_obj.slots["mode"]["value"],
            }
        )
        return response
