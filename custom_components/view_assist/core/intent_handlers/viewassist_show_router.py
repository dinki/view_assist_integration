"""Show router for View Assist: one place for "show" and "display" phrases.

A phrase is split into a name and an optional type word, then handed to the owner:
- a name ending in camera, cameras, cam, cams, feed or feeds goes to the camera intent,
- a name ending in thermostat or thermostats goes to the thermostat intent,
- a name ending in list or lists goes to the list intent,
- "everything on" or "all on" (or "in") before a list name reads the whole list,
- a name ending in the word view, for a view that exists, goes to the change view intent.

A name with no type word is refused, so a camera called Doorbell must be said
"doorbell camera". A name is never passed on as a bare name. Names are read from
Home Assistant each time a phrase is said. Nothing is stored or polled.

The owner handler runs on a copy of the intent with its own intent type, so its
speech and view navigation work as they do for a direct phrase.
"""

from copy import copy
from typing import override

import voluptuous as vol

from homeassistant.helpers.intent import Intent, IntentResponse

from . import IntentDeviceInfo, IntentOverrideHandler
from .viewassist_change_view import VASetChangeViewIntentHandler
from .viewassist_display_camera import (
    CAMERA_WORDS,
    VAViewAssistDisplayCameraIntentHandler,
    resolve_camera,
)
from .viewassist_list_show import VAShowListIntentHandler, find_todo_entity
from .viewassist_list_show_all import VAShowAllListIntentHandler
from .viewassist_thermostat_control import (
    THERMOSTAT_WORDS,
    VAShowThermostatIntentHandler,
    find_thermostats,
    normalise,
)

LIST_WORDS = ("list", "lists")
TYPE_WORDS = CAMERA_WORDS + THERMOSTAT_WORDS + LIST_WORDS
# Words that make a phrase mean all of that type, when no name is given
PLURAL_WORDS = ("cameras", "cams", "feeds", "thermostats", "lists")
VIEW_NAMES = ("clock", "weather", "info", "infopic", "music", "alarm", "intent")


def refuse(intent_obj: Intent, name: str) -> IntentResponse:
    """Return the short not-found response, which the router's responses speak."""
    response = intent_obj.create_response()
    response.async_set_speech_slots({"name": name or "that"})
    return response


def tag_owner(response: IntentResponse, owner: str, **extra) -> IntentResponse:
    """Name the owner in the speech slots, so the router's response picks its wording."""
    response.speech_slots = {**response.speech_slots, "owner": owner, **extra}
    return response


def navigate_owner(intent_obj: Intent, owner_intent: str) -> None:
    """Navigate for the owner's intent, as the view mapping does for a direct phrase.

    The mapping keys on the intent name the conversation reports, which is the
    router's name, so the router asks the device's intents handler for the owner.
    """
    from ...devices.intents import DeviceIntentsHandler  # noqa: PLC0415, TID252

    device_info = IntentDeviceInfo(intent_obj)
    if device_info.entry is None:
        return
    handler = DeviceIntentsHandler.get(intent_obj.hass, device_info.entry)
    if handler is not None:
        handler.navigate_for_intent(owner_intent)


async def delegate(
    intent_obj: Intent, handler, intent_type: str, slots: dict[str, str]
) -> IntentResponse:
    """Run an owner handler on a copy of the intent, under the owner's intent type."""
    owned = copy(intent_obj)
    owned.intent_type = intent_type
    owned.slots = {key: {"value": value} for key, value in slots.items()}
    return await handler.async_handle(owned)


async def show_camera(intent_obj: Intent, rest: str) -> IntentResponse:
    """Show cameras: a named camera or group, or all of them when no name is given."""
    if rest in ("", "all"):
        spoken = "all cameras"
    else:
        spoken = f"{rest} camera"
        kind, _, _ = resolve_camera(intent_obj.hass, spoken)
        if kind == "none":
            return refuse(intent_obj, rest)
    result = await delegate(
        intent_obj,
        VAViewAssistDisplayCameraIntentHandler(),
        "ViewAssistDisplayCamera",
        {"camera": spoken},
    )
    if result.speech_slots.get("kind") != "empty" and not result.speech_slots.get(
        "not_found"
    ):
        navigate_owner(intent_obj, "ViewAssistDisplayCamera")
    return tag_owner(result, "ViewAssistDisplayCamera")


async def show_thermostat(
    intent_obj: Intent, satellite: str | None, rest: str, plural: bool
) -> IntentResponse:
    """Show thermostats: named, all (plural with no name), or the default thermostat."""
    if rest == "all" or (not rest and plural):
        wanted = "all"
    else:
        wanted = rest
    kind, _, _ = find_thermostats(intent_obj.hass, satellite, wanted)
    if kind == "none":
        return refuse(intent_obj, rest or "thermostat")
    result = await delegate(
        intent_obj,
        VAShowThermostatIntentHandler(),
        "ViewAssistShowThermostat",
        {"thermostat": wanted},
    )
    if not result.speech_slots.get("error"):
        navigate_owner(intent_obj, "ViewAssistShowThermostat")
    return tag_owner(result, "ViewAssistShowThermostat")


async def show_list(intent_obj: Intent, rest: str, read_all: bool) -> IntentResponse:
    """Show a named list, or read all of its items when asked to."""
    if not rest or find_todo_entity(intent_obj.hass, rest) is None:
        return refuse(intent_obj, rest or "list")
    if read_all:
        result = await delegate(
            intent_obj,
            VAShowAllListIntentHandler(),
            "ViewAssistShowAllList",
            {"list": rest},
        )
        return tag_owner(result, "ViewAssistShowAllList")
    result = await delegate(
        intent_obj,
        VAShowListIntentHandler(),
        "ViewAssistShowList",
        {"list": rest},
    )
    return tag_owner(result, "ViewAssistShowList")


class VAShowRouterIntentHandler(IntentOverrideHandler):
    """Route a show or display phrase to the camera, thermostat, list or view intent."""

    intent_type = "ViewAssistShowRouter"
    description = """
        Shows a camera, thermostat, list or view on a View Assist satellite. A name must
        end with its type word, such as camera, thermostat or list, unless it is a view
        name such as weather view.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("name"): str,
            vol.Optional("read"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Work out the owner of the phrase and hand it on."""
        slots = intent_obj.slots
        name = normalise(slots["name"]["value"]) if "name" in slots else ""
        # The sentence can capture a leading article ("show the kitchen thermostat")
        words_in = name.split()
        while words_in and words_in[0] in ("the", "my", "a", "an", "our"):
            words_in.pop(0)
        name = " ".join(words_in)
        read_all = bool(slots.get("read")) and normalise(slots["read"]["value"]) == "all"
        for prefix in ("everything on ", "all on ", "everything in ", "all in "):
            if name.startswith(prefix):
                name = name[len(prefix) :].strip()
                read_all = True
                break

        words = name.split()
        last = words[-1] if words else ""
        device_info: IntentDeviceInfo = self.get_device_info(intent_obj)
        satellite = device_info.entity_id if device_info else None

        if read_all and last not in CAMERA_WORDS + THERMOSTAT_WORDS:
            rest = " ".join(words[:-1]) if last in LIST_WORDS else name
            return await show_list(intent_obj, rest, True)

        if last in TYPE_WORDS:
            rest = " ".join(words[:-1])
            plural = last in PLURAL_WORDS
            if last in CAMERA_WORDS:
                if read_all:
                    return refuse(intent_obj, name)
                return await show_camera(intent_obj, rest)
            if last in THERMOSTAT_WORDS:
                if read_all:
                    return refuse(intent_obj, name)
                return await show_thermostat(intent_obj, satellite, rest, plural)
            return await show_list(intent_obj, rest, read_all)

        # No type word: only a view name is allowed
        if name.endswith(" view"):
            view = name[: -len(" view")].strip()
            if view in VIEW_NAMES:
                result = await delegate(
                    intent_obj,
                    VASetChangeViewIntentHandler(),
                    "ViewAssistChangeView",
                    {"view": view},
                )
                return tag_owner(result, "ViewAssistChangeView", view=view)
        return refuse(intent_obj, name)
