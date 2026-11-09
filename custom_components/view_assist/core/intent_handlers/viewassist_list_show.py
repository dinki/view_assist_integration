"""Shared list helpers and the show list intent for View Assist."""

import logging
from typing import override

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.intent import Intent, IntentResponse

from ...const import DOMAIN  # noqa: TID252
from . import IntentDeviceInfo, IntentOverrideHandler
from .viewassist_display_camera import (
    resolve_camera,
    show_camera,
    split_camera_word,
)

_LOGGER = logging.getLogger(__name__)

# Number of unchecked items read out before offering to read the rest
MAX_ITEMS_READOUT = 5
LIST_VIEW_FONT_SIZE = "4vw"
LIST_VIEW = "list"

# Last list read on each satellite: satellite entity_id -> (todo entity_id, items already read)
LAST_READ: dict[str, tuple[str, int]] = {}


def normalise_list_name(name: str) -> str:
    """Lower case the name and strip an optional trailing 'list'."""
    name = str(name).strip().lower()
    if name.endswith(" list"):
        name = name[: -len(" list")]
    return name.strip()


def friendly_name(hass: HomeAssistant, entity_id: str) -> str:
    """Return the friendly name of an entity, falling back to its entity_id."""
    state = hass.states.get(entity_id)
    if state is None:
        return entity_id
    return str(state.attributes.get("friendly_name") or state.name)


def entity_aliases(hass: HomeAssistant, entity_id: str) -> set[str]:
    """Return the Assist aliases set on an entity in the entity registry."""
    entry = er.async_get(hass).async_get(entity_id)
    # Registry aliases are normally text, but skip anything else rather than fail
    return {
        alias
        for alias in (getattr(entry, "aliases", None) or ())
        if isinstance(alias, str) and alias.strip()
    }


def find_todo_entity(hass: HomeAssistant, spoken_name: str) -> str | None:
    """Return the todo entity_id matching the spoken name.

    Friendly names are checked first, then Home Assistant aliases.
    """
    wanted = normalise_list_name(spoken_name)
    states = list(hass.states.async_all("todo"))
    for state in states:
        if normalise_list_name(friendly_name(hass, state.entity_id)) == wanted:
            return state.entity_id
    for state in states:
        if any(
            normalise_list_name(alias) == wanted
            for alias in entity_aliases(hass, state.entity_id)
        ):
            return state.entity_id
    return None


def todo_entities(hass: HomeAssistant) -> list[str]:
    """Return all todo entity_ids."""
    return [state.entity_id for state in hass.states.async_all("todo")]


async def async_get_items(
    hass: HomeAssistant, entity_id: str, status: str | None = None
) -> list[dict]:
    """Return todo items, optionally filtered by status."""
    data = {"entity_id": entity_id}
    if status:
        data["status"] = status
    result = await hass.services.async_call(
        "todo", "get_items", data, blocking=True, return_response=True
    )
    return result.get(entity_id, {}).get("items", [])


def satellite_key(satellite: str | None) -> str:
    """Return the key used to remember state for a satellite."""
    return satellite or ""


async def async_show_list_view(
    hass: HomeAssistant, intent_obj: Intent, satellite: str | None, entity_id: str
) -> None:
    """Set the satellite's list and title so the list view shows the right list."""
    if not satellite:
        _LOGGER.debug("No satellite found to show list %s", entity_id)
        return
    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            "title": friendly_name(hass, entity_id),
            "message_font_size": LIST_VIEW_FONT_SIZE,
            "list": entity_id,
        },
        blocking=True,
        context=intent_obj.context,
        target={"entity_id": satellite},
    )


async def async_show_camera_phrase(
    intent_obj: Intent, response: IntentResponse, spoken_name: str
) -> bool:
    """Show a camera for a phrase the list intent received. Return False if no camera matches."""
    kind, name, entity_ids = resolve_camera(intent_obj.hass, spoken_name)
    if kind == "none":
        return False
    if kind != "empty":
        show_camera(intent_obj.hass, IntentDeviceInfo(intent_obj), entity_ids)
    response.async_set_speech_slots({"camera_kind": kind, "camera_name": name})
    return True


async def async_navigate_to_list_view(intent_obj: Intent, satellite: str | None) -> None:
    """Navigate the satellite to the list view. Done here, not by the view mapping."""
    if not satellite:
        return
    entry = IntentDeviceInfo(intent_obj).entry
    if entry is None:
        return
    dashboard = entry.runtime_data.dashboard.dashboard or "/view-assist"
    await intent_obj.hass.services.async_call(
        DOMAIN,
        "navigate",
        {"device": satellite, "path": f"/{dashboard.strip('/')}/{LIST_VIEW}"},
        blocking=True,
        context=intent_obj.context,
    )


class VAShowListIntentHandler(IntentOverrideHandler):
    """Show a named todo list and read the first few unchecked items."""

    intent_type = "ViewAssistShowList"
    description = """
        Shows a named todo list, such as the shopping list, on a View Assist satellite
        and reads the first few unchecked items. The list slot is the friendly name of
        the list without the word list.
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
        """Read the first unchecked items and show the list."""
        hass = intent_obj.hass
        spoken_name = intent_obj.slots["list"]["value"]
        response = intent_obj.create_response()

        # A phrase ending in a camera word is a camera request. Otherwise try lists first.
        _, camera_word = split_camera_word(spoken_name)
        if camera_word and await async_show_camera_phrase(
            intent_obj, response, spoken_name
        ):
            return response

        entity_id = find_todo_entity(hass, spoken_name)
        if entity_id is None:
            if not camera_word and await async_show_camera_phrase(
                intent_obj, response, spoken_name
            ):
                return response
            response.async_set_speech_slots(
                {
                    "found": False,
                    "spoken_name": spoken_name,
                    "camera_not_found": camera_word,
                }
            )
            return response

        items = [
            item["summary"]
            for item in await async_get_items(hass, entity_id, "needs_action")
        ]

        device_info = self.get_device_info(intent_obj)
        satellite = device_info.entity_id if device_info else None
        LAST_READ[satellite_key(satellite)] = (
            entity_id,
            min(MAX_ITEMS_READOUT, len(items)),
        )

        # Speech text lives in the custom sentence responses, not here
        response.async_set_speech_slots(
            {
                "found": True,
                "spoken_name": spoken_name,
                "list_name": friendly_name(hass, entity_id),
                "count": len(items),
                "item_names": items[:MAX_ITEMS_READOUT],
            }
        )
        await async_show_list_view(hass, intent_obj, satellite, entity_id)
        await async_navigate_to_list_view(intent_obj, satellite)
        return response
