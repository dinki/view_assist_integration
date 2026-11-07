"""Show a todo list and read every unchecked item."""

from typing import override

import voluptuous as vol

from homeassistant.helpers.intent import Intent, IntentResponse

from . import IntentOverrideHandler
from .viewassist_list_show import (
    LAST_READ,
    async_get_items,
    async_show_list_view,
    find_todo_entity,
    friendly_name,
    satellite_key,
)


class VAShowAllListIntentHandler(IntentOverrideHandler):
    """Show a named todo list and read every unchecked item."""

    intent_type = "ViewAssistShowAllList"
    description = """
        Shows a named todo list on a View Assist satellite and reads every unchecked
        item. The list slot is the friendly name of the list without the word list.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("list"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Read every unchecked item and show the list."""
        hass = intent_obj.hass
        spoken_name = intent_obj.slots["list"]["value"]
        response = intent_obj.create_response()

        entity_id = find_todo_entity(hass, spoken_name)
        if entity_id is None:
            response.async_set_speech_slots(
                {"found": False, "spoken_name": spoken_name}
            )
            return response

        items = [
            item["summary"]
            for item in await async_get_items(hass, entity_id, "needs_action")
        ]

        device_info = self.get_device_info(intent_obj)
        satellite = device_info.entity_id if device_info else None
        LAST_READ[satellite_key(satellite)] = (entity_id, len(items))

        response.async_set_speech_slots(
            {
                "found": True,
                "spoken_name": spoken_name,
                "list_name": friendly_name(hass, entity_id),
                "count": len(items),
                "item_names": items,
            }
        )
        await async_show_list_view(hass, intent_obj, satellite, entity_id)
        return response
