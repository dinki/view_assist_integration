"""Check whether an item is on a todo list and whether it is checked off."""

from typing import override

import voluptuous as vol

from homeassistant.helpers.intent import Intent, IntentResponse

from . import IntentOverrideHandler
from .viewassist_list_show import async_get_items, find_todo_entity, friendly_name


def _norm(text: str) -> str:
    return text.strip().lower()


class VACheckListItemIntentHandler(IntentOverrideHandler):
    """Answer whether an item is on a named todo list."""

    intent_type = "ViewAssistCheckListItem"
    description = """
        Checks whether an item is on a named todo list and whether it has been checked
        off. The list slot is the friendly name of the list without the word list.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("item"): str,
            vol.Required("list"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Report whether the item is unchecked, checked, or missing."""
        hass = intent_obj.hass
        spoken_item = intent_obj.slots["item"]["value"]
        spoken_name = intent_obj.slots["list"]["value"]
        response = intent_obj.create_response()

        entity_id = find_todo_entity(hass, spoken_name)
        if entity_id is None:
            response.async_set_speech_slots(
                {"status": "list_not_found", "spoken_name": spoken_name}
            )
            return response

        wanted = _norm(spoken_item)
        matches = [
            item
            for item in await async_get_items(hass, entity_id)
            if _norm(item["summary"]) == wanted
        ]
        if any(item.get("status") == "needs_action" for item in matches):
            status = "unchecked"
        elif matches:
            status = "checked"
        else:
            status = "missing"

        response.async_set_speech_slots(
            {
                "status": status,
                "spoken_item": spoken_item,
                "list_name": friendly_name(hass, entity_id),
            }
        )
        return response
