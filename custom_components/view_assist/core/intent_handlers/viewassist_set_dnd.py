"""Assist Satellite intents."""

import logging
from typing import override

import voluptuous as vol

from homeassistant.helpers import intent
from homeassistant.helpers.dispatcher import async_dispatcher_send

from ...const import DOMAIN  # noqa: TID252
from ...typed import VAEvent, VAEventType  # noqa: TID252
from . import IntentOverrideHandler

_LOGGER = logging.getLogger(__name__)

# Spoken values mapped to the stored "on"/"off" value
DND_ON = "on"
DND_OFF = "off"


class VASetDNDIntentHandler(IntentOverrideHandler):
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
        dnd_state = intent_obj.slots["dnd_setting"]["value"] == DND_ON

        # Send do not disturb status change event
        device_info = self.get_device_info(intent_obj)
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_{device_info.entry_id}_event",
            VAEvent(VAEventType.STATUS_ITEM_CHANGE, {"do_not_disturb": dnd_state}),
        )

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {"dnd_setting": DND_ON if dnd_state else DND_OFF}
        )
        return response
