"""Read the items on a list that were not read out yet."""

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


class VAReadRestListIntentHandler(IntentOverrideHandler):
    """Read the remaining unchecked items after the first few."""

    intent_type = "ViewAssistReadRestList"
    description = """
        Reads the unchecked items on a todo list that were not read out yet. The list
        is optional; if omitted, the list read most recently on this satellite is used.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("list"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Read the remaining items from where the last read stopped."""
        hass = intent_obj.hass
        response = intent_obj.create_response()
        device_info = self.get_device_info(intent_obj)
        satellite = device_info.entity_id if device_info else None
        key = satellite_key(satellite)

        spoken_name = intent_obj.slots.get("list", {}).get("value")
        if spoken_name:
            entity_id = find_todo_entity(hass, spoken_name)
            if entity_id is None:
                response.async_set_speech_slots(
                    {"status": "not_found", "spoken_name": spoken_name}
                )
                return response
        else:
            if key not in LAST_READ:
                response.async_set_speech_slots({"status": "no_list"})
                return response
            entity_id = LAST_READ[key][0]

        # Only continue from the last read position if it was the same list
        offset = 0
        if key in LAST_READ and LAST_READ[key][0] == entity_id:
            offset = LAST_READ[key][1]

        items = [
            item["summary"]
            for item in await async_get_items(hass, entity_id, "needs_action")
        ]
        remaining = items[offset:]
        LAST_READ[key] = (entity_id, len(items))

        response.async_set_speech_slots(
            {
                "status": "ok" if remaining else "nothing_more",
                "spoken_name": spoken_name or "",
                "list_name": friendly_name(hass, entity_id),
                "count": len(remaining),
                "item_names": remaining,
            }
        )
        await async_show_list_view(hass, intent_obj, satellite, entity_id)
        return response
