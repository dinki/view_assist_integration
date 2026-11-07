"""Add an item to a todo list, without adding duplicates of unchecked items."""

import logging
import re
from typing import override

import voluptuous as vol

from homeassistant.helpers.intent import Intent, IntentResponse

from . import IntentOverrideHandler
from .viewassist_list_show import (
    async_get_items,
    async_show_list_view,
    find_todo_entity,
    friendly_name,
)

_LOGGER = logging.getLogger(__name__)


def _norm(text: str) -> str:
    """Normalise for comparison: ignore case, punctuation and extra spaces."""
    text = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(text.split())


class VAListAddIntentHandler(IntentOverrideHandler):
    """Add an item to a named list, skipping items already unchecked on it."""

    intent_type = "ViewAssistListAdd"
    description = """
        Adds an item to a named todo list and shows the list. If the item is already
        unchecked on the list, it is not added again. The list is the friendly name or
        alias of the list without the word list.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("item"): str,
            vol.Required("name"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Add the item unless it is already unchecked on the list."""
        hass = intent_obj.hass
        spoken_item = intent_obj.slots["item"]["value"]
        spoken_name = intent_obj.slots["name"]["value"]
        response = intent_obj.create_response()

        entity_id = find_todo_entity(hass, spoken_name)
        if entity_id is None:
            response.async_set_speech_slots(
                {"status": "list_not_found", "spoken_name": spoken_name}
            )
            return response

        list_name = friendly_name(hass, entity_id)
        # Filter by status here rather than relying on the service's status argument
        all_items = await async_get_items(hass, entity_id)
        unchecked = [item["summary"] for item in all_items if item.get("status") == "needs_action"]
        wanted = _norm(spoken_item)
        existing = [_norm(summary) for summary in unchecked]
        _LOGGER.debug(
            "Add item '%s' to %s: wanted=%r existing unchecked=%r",
            spoken_item,
            entity_id,
            wanted,
            existing,
        )

        if wanted in existing:
            status = "already"
        else:
            await hass.services.async_call(
                "todo",
                "add_item",
                {"entity_id": entity_id, "item": spoken_item},
                blocking=True,
                context=intent_obj.context,
            )
            status = "added"

        response.async_set_speech_slots(
            {
                "status": status,
                "spoken_name": spoken_name,
                "item": spoken_item,
                "list_name": list_name,
            }
        )

        device_info = self.get_device_info(intent_obj)
        satellite = device_info.entity_id if device_info else None
        await async_show_list_view(hass, intent_obj, satellite, entity_id)
        return response
