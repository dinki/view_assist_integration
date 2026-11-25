"""Add an item to a todo list, without adding duplicates of unchecked items."""

import logging
from typing import override

import voluptuous as vol

from homeassistant.components.todo import (
    DATA_COMPONENT as TODO_DATA_COMPONENT,
    DOMAIN as TODO_DOMAIN,
    TodoItem,
    TodoListEntity,
)
from homeassistant.components.todo.const import TodoItemStatus
from homeassistant.components.todo.intent import INTENT_LIST_ADD_ITEM
from homeassistant.helpers import intent
from homeassistant.helpers.intent import Intent, IntentResponse

from ...const import LIST_VIEW_FONT_SIZE  # noqa: TID252
from ...devices.status import StatusManager  # noqa: TID252
from . import IntentOverrideHandler, VAIntentResponseErrorCode

_LOGGER = logging.getLogger(__name__)

LIST_SUBSET_ITEM_COUNT = 5


class VAListAddItemIntentHandler(IntentOverrideHandler):
    """Add an item to a named list, skipping items already unchecked on it."""

    intent_type = "ViewAssistListAdd"
    description = """
        Add item to a todo list. If the item is already
        on the list and unchecked, do not add it again. The name slot should be the friendly name of the list.
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

        slots = self.async_validate_slots(intent_obj.slots)
        item = slots["item"]["value"].strip()

        match_result = self.get_matched_targets(
            intent_obj,
            domains={TODO_DOMAIN},
            single_target=True,
            raise_on_no_match=False,
        )

        response = self.create_override_handler_response(intent_obj)

        if not match_result.is_match:
            response.async_set_error(VAIntentResponseErrorCode.NOT_FOUND)
            return response

        target_list = hass.data[TODO_DATA_COMPONENT].get_entity(
            match_result.states[0].entity_id
        )

        # Check not already on list
        for list_item in target_list.todo_items:
            if list_item.summary.lower() == item.lower():
                response.async_set_error(VAIntentResponseErrorCode.ALREADY_EXISTS)
                return response

        await self.add_to_list(target_list, item)

        # Return a response indicating the item was added
        response.async_set_speech_slots(
            {
                "name": target_list.name,
                "item": item,
            }
        )

        response.async_set_view_data(
            {
                "title": target_list.name,
                "message_font_size": LIST_VIEW_FONT_SIZE,
                "list": target_list.entity_id,
            },
        )
        return response

    async def add_to_list(self, target_list: TodoListEntity, item: str) -> None:
        """Add an item to the list if it is not already unchecked."""
        # Format item summary with first letter capitalized and rest as-is
        summary = item[:1].upper() + item[1:] if item else item

        # Add to list
        await target_list.async_create_todo_item(
            TodoItem(summary=summary, status=TodoItemStatus.NEEDS_ACTION)
        )


class VAListCheckItemIntentHandler(IntentOverrideHandler):
    """Answer whether an item is on a named todo list."""

    intent_type = "ViewAssistCheckListItem"
    description = "Checks if an item is on the specified todo list."

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
        """Check if the item is on the specified todo list."""
        hass = intent_obj.hass

        slots = self.async_validate_slots(intent_obj.slots)
        item = slots["item"]["value"].strip()

        match_result = self.get_matched_targets(
            intent_obj,
            domains={TODO_DOMAIN},
            single_target=True,
            raise_on_no_match=False,
        )

        response = self.create_override_handler_response(intent_obj)

        if not match_result.is_match:
            response.async_set_error(VAIntentResponseErrorCode.NOT_FOUND)
            return response

        target_list = hass.data[TODO_DATA_COMPONENT].get_entity(
            match_result.states[0].entity_id
        )

        # Find item in list
        matching_item = None
        for todo_item in target_list.todo_items or ():
            if (
                item == todo_item.uid
                or item.casefold() == (todo_item.summary or "").casefold()
            ) and todo_item.status == TodoItemStatus.NEEDS_ACTION:
                matching_item = todo_item
                break

        if not matching_item:
            response.async_set_error(VAIntentResponseErrorCode.ITEM_NOT_FOUND)
            return response

        status = "unknown"
        if matching_item.status == TodoItemStatus.NEEDS_ACTION:
            status = "unchecked"
        elif matching_item.status == TodoItemStatus.COMPLETED:
            status = "checked"

        response.async_set_speech_slots(
            {
                "name": target_list.name,
                "item": item,
                "status": status,
            }
        )
        return response


class VAListNamesIntentHandler(IntentOverrideHandler):
    """Speak the friendly names of all todo lists."""

    intent_type = "ViewAssistListNames"
    description = "Lists the names of all exposed todo lists in Home Assistant."

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return None

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Return the list names as speech slots."""
        hass = intent_obj.hass

        # Find matching list
        match_constraints = intent.MatchTargetsConstraints(
            domains=[TODO_DOMAIN], assistant=intent_obj.assistant
        )
        match_result = intent.async_match_targets(hass, match_constraints)
        if not match_result.is_match:
            raise intent.MatchFailedError(
                result=match_result, constraints=match_constraints
            )

        target_lists = match_result.states
        lists = sorted(
            [todo_list.attributes.get("friendly_name") for todo_list in target_lists]
        )
        response = self.create_override_handler_response(intent_obj)
        response.async_set_speech_slots({"lists": lists})
        return response


class VAShowListIntentHandler(IntentOverrideHandler):
    """Show a named todo list and read every unchecked item."""

    intent_type = "ViewAssistShowList"
    description = "Speaks each item on the named todo list"

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("name"): str,
            vol.Optional("all_list"): bool,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Read every unchecked item and show the list."""
        hass = intent_obj.hass

        if "all_list" in intent_obj.slots:
            all_list = intent_obj.slots.get("all_list", {}).get("value", False)
        else:
            all_list = False

        match_result = self.get_matched_targets(
            intent_obj,
            domains={TODO_DOMAIN},
            single_target=True,
            raise_on_no_match=False,
        )

        if match_result.is_match:
            target_list = hass.data[TODO_DATA_COMPONENT].get_entity(
                match_result.states[0].entity_id
            )
        else:
            target_list = None

        response = self.create_override_handler_response(intent_obj)

        if target_list is None:
            response.async_set_error(VAIntentResponseErrorCode.NOT_FOUND)
            return response

        items = [
            todo_item.summary
            for todo_item in target_list.todo_items
            if todo_item.status != TodoItemStatus.COMPLETED
        ]

        response.async_set_speech_slots(
            {
                "name": target_list.name,
                "count": len(items),
                "item_names": items if all_list else items[:LIST_SUBSET_ITEM_COUNT],
            }
        )

        response.async_set_view_data(
            {
                "title": target_list.name,
                "message_font_size": LIST_VIEW_FONT_SIZE,
                "list": target_list.entity_id,
            },
        )

        # If didnt real all list, save the last read list in temporary memory
        if not all_list:
            device_info = self.get_device_info(intent_obj)
            sm = StatusManager.get(hass, device_info.entry)
            sm.memory.memorise("last_read_list", target_list.entity_id)

        return response


class VAReadRestListIntentHandler(IntentOverrideHandler):
    """Read the remaining unchecked items from the previously read list."""

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
            vol.Optional("name"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Read the remaining items from where the last read stopped."""

        hass = intent_obj.hass
        device_info = self.get_device_info(intent_obj)

        # Get list from device status
        sm = StatusManager.get(hass, device_info.entry)
        last_list = sm.memory.recall("last_read_list")

        response = self.create_override_handler_response(intent_obj)

        # Compare if asked for list
        if "name" in intent_obj.slots:
            match_result = self.get_matched_targets(
                intent_obj,
                domains={TODO_DOMAIN},
                single_target=True,
                raise_on_no_match=False,
            )

            if match_result.is_match:
                matched_list = match_result.states[0].entity_id
                if last_list and last_list != matched_list:
                    response.async_set_error(VAIntentResponseErrorCode.NOT_ACTIVE_LIST)
                    return response

        if not last_list:
            response.async_set_error(VAIntentResponseErrorCode.NOT_FOUND)
            return response

        target_list = hass.data[TODO_DATA_COMPONENT].get_entity(last_list)
        items = [
            todo_item.summary
            for todo_item in target_list.todo_items
            if todo_item.status != TodoItemStatus.COMPLETED
        ]

        response.async_set_speech_slots(
            {
                "name": target_list.name,
                "item_names": items,
            }
        )

        response.async_set_view_data(
            {
                "title": target_list.name,
                "message_font_size": LIST_VIEW_FONT_SIZE,
                "list": target_list.entity_id,
            },
        )

        # Clear the last read list from memory
        sm.memory.forget("last_read_list")

        return response
