"""Assist Satellite intents."""

from typing import override

import voluptuous as vol

from homeassistant.helpers import intent

from ...devices.navigation import NavigationManager  # noqa: TID252
from . import IntentOverrideHandler


class VASetChangeViewIntentHandler(IntentOverrideHandler):
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
        device_info = self.get_device_info(intent_obj)

        if device_info.entry is None:
            response = intent_obj.create_response()
            response.async_set_speech("I could not find the satellite for that view.")
            return response

        # Retrieve the dashboard path now that the config entry is available
        dashboard_path = device_info.entry.runtime_data.dashboard.dashboard

        nm = NavigationManager.get(hass, device_info.entry)
        nm.browser_navigate(f"{dashboard_path}/{view}")

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {
                "view": view,
            }
        )
        return response
