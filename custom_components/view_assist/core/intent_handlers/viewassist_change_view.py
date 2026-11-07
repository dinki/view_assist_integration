"""Assist Satellite intents."""

from typing import override

import voluptuous as vol

from homeassistant.helpers import intent

from ...const import DOMAIN  # noqa: TID252

from . import IntentDeviceInfo
from ...typed import VAConfigEntry


class VASetChangeViewIntentHandler(intent.IntentHandler):
    """Change View Assist View."""

    intent_type = "ViewAssistChangeView"
    description = """
        Changes the displayed view on a View Assist satellite through voice command.
    """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("view"): str,
        }

    @override
    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
        """Change View Assist view."""
        hass = intent_obj.hass
        view = intent_obj.slots["view"]["value"]

        # Work out the satellite from the intent itself. The voice path does not
        # pass extra_data, so an entity_id from extra_data is used only if given.
        device_info = IntentDeviceInfo(intent_obj)
        entity_id = (
            extra_data["entity_id"]
            if extra_data and "entity_id" in extra_data
            else device_info.entity_id
        )
        entry: VAConfigEntry = device_info.entry

        if entry is None:
            response = intent_obj.create_response()
            response.async_set_speech("I could not find the satellite for that view.")
            return response

        # Retrieve the dashboard path now that the config entry is available
        dashboard_path = entry.runtime_data.dashboard.dashboard

        await hass.services.async_call(
            DOMAIN,
            "navigate",
            {
                "device": entity_id,
                "path": f"{dashboard_path}/{view}"
            },
            blocking=True,
            context=intent_obj.context,
        )

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {
                "view": view,
            }
        )
        return response