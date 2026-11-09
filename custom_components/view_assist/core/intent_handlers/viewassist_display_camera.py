"""Display camera intent handler.

Names are matched in this order, using only cameras exposed to Assist:
1. Exact camera friendly name or alias. More than one exact match is a group.
2. Label name (group).
3. Area name or alias (group), only when the word cameras is said.
4. Partial camera name, only when the camera word is said.
"all" means every exposed, available camera.

The satellite's cameras attribute is set to a list of entity IDs, which the camera
card shows. The list intent uses the same matching, so phrases such as
"show kitchen cameras" work whichever intent hears them. When a phrase is not a
camera but is a list, this handler hands it to the list intent.
"""

from copy import copy
from typing import override

import voluptuous as vol

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.intent import Intent, IntentResponse

from ...const import DOMAIN, VAMode  # noqa: TID252
from ...devices.status import StatusManager  # noqa: TID252
from ...typed import VAEvent, VAEventType  # noqa: TID252
from . import IntentDeviceInfo, IntentOverrideHandler

CAMERA_WORDS = ("camera", "cameras", "cam", "cams", "feed", "feeds")
ALL_CAMERAS = "all"
ASSIST_ASSISTANT = "conversation"
UNAVAILABLE_STATES = ("unknown", "unavailable", "None")


def split_camera_word(name: object) -> tuple[str, bool]:
    """Return the name without a trailing camera word, lower case, and whether one was there."""
    words = str(name).strip().lower().split()
    had_word = bool(words) and words[-1] in CAMERA_WORDS
    if had_word:
        words = words[:-1]
    return " ".join(words), had_word


def display_name(name: str) -> str:
    """Return the name as it was said, without a trailing camera word."""
    words = name.strip().split()
    if len(words) > 1 and words[-1].lower() in CAMERA_WORDS:
        words = words[:-1]
    return " ".join(words) or name.strip()


def exposed_cameras(hass: HomeAssistant) -> list[State]:
    """Return camera states that are exposed to Assist and currently available."""
    return [
        state
        for state in hass.states.async_all("camera")
        if state.state not in UNAVAILABLE_STATES
        and async_should_expose(hass, ASSIST_ASSISTANT, state.entity_id)
    ]


def camera_names(hass: HomeAssistant, state: State) -> set[str]:
    """Return the normalised friendly name and Assist aliases of a camera."""
    names = {split_camera_word(state.attributes.get("friendly_name") or state.name)[0]}
    entry = er.async_get(hass).async_get(state.entity_id)
    for alias in getattr(entry, "aliases", None) or ():
        if isinstance(alias, str) and alias.strip():
            names.add(split_camera_word(alias)[0])
    names.discard("")
    return names


def label_cameras(
    hass: HomeAssistant, cameras: list[State], wanted: str
) -> list[State] | None:
    """Return the cameras with the label named wanted, or None when no label has that name."""
    label_id = next(
        (
            label.label_id
            for label in lr.async_get(hass).async_list_labels()
            if split_camera_word(label.name)[0] == wanted
        ),
        None,
    )
    if label_id is None:
        return None
    entity_ids = {
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if label_id in (entry.labels or ())
    }
    return [state for state in cameras if state.entity_id in entity_ids]


def camera_area_id(hass: HomeAssistant, state: State) -> str | None:
    """Return the area of a camera: its own area, or the area of its device."""
    entry = er.async_get(hass).async_get(state.entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
        return device.area_id
    return None


def area_cameras(
    hass: HomeAssistant, cameras: list[State], wanted: str
) -> list[State] | None:
    """Return the cameras in the area named wanted, or None when no area has that name."""
    area_id = None
    for area in ar.async_get(hass).async_list_areas():
        names = {str(area.name).strip().lower()}
        for alias in getattr(area, "aliases", None) or ():
            if isinstance(alias, str) and alias.strip():
                names.add(alias.strip().lower())
        if wanted in names:
            area_id = area.id
            break
    if area_id is None:
        return None
    return [state for state in cameras if camera_area_id(hass, state) == area_id]


def resolve_camera(hass: HomeAssistant, spoken: str) -> tuple[str, str, list[str]]:
    """Return (kind, speech name, entity IDs) for a spoken name.

    Kind is all, single, group, empty or none. Empty means a label or area
    matched but has no exposed cameras. The speech name is the name as said.
    """
    wanted, had_word = split_camera_word(spoken)
    speech_name = display_name(spoken)
    cameras = exposed_cameras(hass)

    if wanted == ALL_CAMERAS:
        ids = [state.entity_id for state in cameras]
        return ("all", "", ids) if ids else ("empty", "", [])
    if not wanted:
        return "none", spoken, []

    exact = [s for s in cameras if wanted in camera_names(hass, s)]
    if len(exact) == 1:
        return "single", speech_name, [exact[0].entity_id]
    if exact:
        return "group", speech_name, [s.entity_id for s in exact]

    labelled = label_cameras(hass, cameras, wanted)
    if labelled is not None:
        ids = [s.entity_id for s in labelled]
        return ("group", speech_name, ids) if ids else ("empty", speech_name, [])

    if had_word:
        in_area = area_cameras(hass, cameras, wanted)
        if in_area is not None:
            ids = [s.entity_id for s in in_area]
            return ("group", speech_name, ids) if ids else ("empty", speech_name, [])

        partial = [s for s in cameras if any(wanted in n for n in camera_names(hass, s))]
        if len(partial) == 1:
            return "single", speech_name, [partial[0].entity_id]
        if partial:
            return "group", speech_name, [s.entity_id for s in partial]

    return "none", spoken, []


def show_camera(hass: HomeAssistant, device_info: IntentDeviceInfo, entity_ids: list[str]) -> None:
    """Set the cameras on the satellite status and switch the satellite to camera mode."""
    if device_info.entry is None:
        return
    sm = StatusManager.get(hass, device_info.entry)
    sm.cameras = entity_ids
    async_dispatcher_send(
        hass,
        f"{DOMAIN}_{device_info.entry_id}_event",
        VAEvent(VAEventType.MODE_CHANGE, {"mode": VAMode.CAMERA}),
    )


async def hand_off_to_list(intent_obj: Intent, spoken: str) -> IntentResponse:
    """Run the list intent for a phrase that is a list name. The list speech is used."""
    # Imported here: the list intent imports this module
    from .viewassist_list_show import VAShowListIntentHandler  # noqa: PLC0415

    list_intent = copy(intent_obj)
    list_intent.intent_type = "ViewAssistShowList"
    list_intent.slots = {"list": {"value": spoken}}
    return await VAShowListIntentHandler().async_handle(list_intent)


class VAViewAssistCancelCameraIntentHandler(IntentOverrideHandler):
    """Stop camera through View Assist."""

    intent_type = "ViewAssistCancelCamera"
    description = """
        Cancel camera through a View Assist satellite via voice command.
    """

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Cancel camera through View Assist."""
        hass = intent_obj.hass
        device_info = self.get_device_info(intent_obj)

        # Send display camera event
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_{device_info.entry_id}_event",
            VAEvent(VAEventType.MODE_CHANGE, {"mode": VAMode.NORMAL}),
        )

        return intent_obj.create_response()


class VAViewAssistDisplayCameraIntentHandler(IntentOverrideHandler):
    """Display camera through View Assist."""

    intent_type = "ViewAssistDisplayCamera"
    description = """
        Display camera through a View Assist satellite via voice command.
    """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("camera"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Display camera through View Assist."""
        hass = intent_obj.hass
        camera = (
            intent_obj.slots["camera"]["value"]
            if "camera" in intent_obj.slots
            else ALL_CAMERAS
        )
        device_info = self.get_device_info(intent_obj)

        kind, name, entity_ids = resolve_camera(hass, camera)
        if kind == "none":
            # Not a camera. If it is a list, the list intent answers it.
            # Imported here: the list intent imports this module
            from .viewassist_list_show import find_todo_entity  # noqa: PLC0415

            if find_todo_entity(hass, camera):
                return await hand_off_to_list(intent_obj, camera)

        if kind in ("single", "group", "all"):
            show_camera(hass, device_info, entity_ids)

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {"kind": kind, "name": name, "not_found": kind == "none"}
        )
        return response
