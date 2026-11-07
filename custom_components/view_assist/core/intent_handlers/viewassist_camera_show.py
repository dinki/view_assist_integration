"""Show a camera, a group of cameras, or all cameras on a View Assist satellite.

Names are matched in this order: exposed camera (friendly name and Assist aliases),
label (group), area (group, only when the word cameras is said), then partial
camera name (only when the camera word is said). "All" means every exposed camera.

The camera intent and the list intent both use these functions. The list intent
hands camera phrases here, so "show garage camera" works whichever intent matches.
The handler navigates to the camera view itself, because the view mapping cannot
carry the camera query string.
"""

import logging
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
from homeassistant.helpers.intent import Intent

from ...const import DOMAIN  # noqa: TID252
from . import IntentDeviceInfo, IntentOverrideHandler, IntentOverrideResponse

_LOGGER = logging.getLogger(__name__)

ASSIST_ASSISTANT = "conversation"
CAMERA_VIEW = "camera"
DEFAULT_DASHBOARD = "/view-assist"
# Reserved spoken name meaning every exposed camera
ALL_CAMERAS = "all"
CAMERA_WORDS = ("camera", "cameras", "cam", "cams", "feed", "feeds")
UNAVAILABLE_STATES = ("unknown", "unavailable", "None")
FOUND_KINDS = ("single", "group", "all")


def split_camera_word(name: object) -> tuple[str, bool]:
    """Return the name without a trailing camera word, lower case, and whether one was there."""
    words = str(name).strip().lower().split()
    had_word = bool(words) and words[-1] in CAMERA_WORDS
    if had_word:
        words = words[:-1]
    return " ".join(words), had_word


def display_name(name: str) -> str:
    """Return the name with its original case and without a trailing camera word."""
    words = name.strip().split()
    if len(words) > 1 and words[-1].lower() in CAMERA_WORDS:
        words = words[:-1]
    return " ".join(words) or name.strip()


def friendly_name(state: State) -> str:
    """Return the friendly name of a state, falling back to its name."""
    return state.attributes.get("friendly_name") or state.name


def camera_names(hass: HomeAssistant, state: State) -> set[str]:
    """Return the normalised friendly name and Assist aliases of a camera."""
    names = {split_camera_word(friendly_name(state))[0]}
    entry = er.async_get(hass).async_get(state.entity_id)
    for alias in getattr(entry, "aliases", None) or ():
        if isinstance(alias, str) and alias.strip():
            names.add(split_camera_word(alias)[0])
        else:
            _LOGGER.warning("Ignoring invalid alias %r on %s", alias, state.entity_id)
    names.discard("")
    return names


def exposed_cameras(hass: HomeAssistant) -> list[State]:
    """Return camera states that are exposed to Assist and currently available."""
    return [
        state
        for state in hass.states.async_all("camera")
        if state.state not in UNAVAILABLE_STATES
        and async_should_expose(hass, ASSIST_ASSISTANT, state.entity_id)
    ]


def label_cameras(
    hass: HomeAssistant, cameras: list[State], wanted: str
) -> list[State] | None:
    """Return the cameras in the label named wanted, or None when no label has that name."""
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


def resolve_cameras(hass: HomeAssistant, spoken: str) -> tuple[str, str, list[State]]:
    """Return (kind, speech name, cameras) for a spoken name.

    Kind is all, single, group, empty or none. The speech name is the name as it
    was said, so the reply matches the request.
    """
    wanted, had_word = split_camera_word(spoken)
    cameras = exposed_cameras(hass)
    speech_name = display_name(spoken)

    if wanted == ALL_CAMERAS:
        return "all", "all", cameras
    if not wanted:
        return "none", spoken, []

    exact = [s for s in cameras if wanted in camera_names(hass, s)]
    if len(exact) == 1:
        return "single", speech_name, exact
    if exact:
        return "group", speech_name, exact

    labelled = label_cameras(hass, cameras, wanted)
    if labelled is not None:
        if labelled:
            return "group", speech_name, labelled
        return "empty", speech_name, []

    if had_word:
        in_area = area_cameras(hass, cameras, wanted)
        if in_area is not None:
            if in_area:
                return "group", speech_name, in_area
            return "empty", speech_name, []

        partial = [
            s for s in cameras if any(wanted in n for n in camera_names(hass, s))
        ]
        if len(partial) == 1:
            return "single", speech_name, partial
        if partial:
            return "group", speech_name, partial

    return "none", spoken, []


def camera_query(kind: str, cameras: list[State]) -> str:
    """Return the camera view query string the card reads. Hold is not passed in the URL."""
    if kind == "single":
        return f"?camera={cameras[0].entity_id}"
    entity_ids = ",".join(state.entity_id for state in cameras)
    return f"?show=configured&cameras={entity_ids}"


async def async_show_cameras(
    intent_obj: Intent, kind: str, cameras: list[State]
) -> None:
    """Navigate the satellite to the camera view for a found result."""
    if kind not in FOUND_KINDS:
        return
    hass = intent_obj.hass
    device = IntentDeviceInfo(intent_obj)
    satellite = device.entity_id
    entry = device.entry
    if not satellite or entry is None:
        _LOGGER.debug("No satellite found to show cameras")
        return
    dashboard = entry.runtime_data.dashboard.dashboard or DEFAULT_DASHBOARD
    view_path = f"/{dashboard.strip('/')}/{CAMERA_VIEW}"
    path = f"{view_path}{camera_query(kind, cameras)}"
    # Clear the title first, so a title from an earlier view (such as the list) is not shown
    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {"title": ""},
        blocking=True,
        context=intent_obj.context,
        target={"entity_id": satellite},
    )
    await hass.services.async_call(
        DOMAIN,
        "navigate",
        {"device": satellite, "path": path},
        blocking=True,
        context=intent_obj.context,
    )
    # Hold first. Setting hold records the view still on screen, which can be the old one,
    # so hold_view is set after it, with no query string.
    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {"hold": True},
        blocking=True,
        context=intent_obj.context,
        target={"entity_id": satellite},
    )
    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {"hold_view": view_path},
        blocking=True,
        context=intent_obj.context,
        target={"entity_id": satellite},
    )


class VAShowCameraIntentHandler(IntentOverrideHandler):
    """Show one camera, a group of cameras, or all cameras."""

    intent_type = "ViewAssistShowCamera"
    description = """
        Shows an exposed camera, a group of cameras named by a label or an area
        (for example 'kitchen cameras'), or all cameras on a View Assist satellite.
        The name slot is the friendly name or alias, with or without the word camera.
        Speech is in the sentence file.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {vol.Required("name"): str}

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentOverrideResponse:
        """Resolve the camera names, navigate the satellite and set the speech slots."""
        spoken = str((intent_obj.slots.get("name") or {}).get("value") or "").strip()
        response = self.create_override_handler_response(intent_obj)
        kind, name, cameras = resolve_cameras(intent_obj.hass, spoken)
        await async_show_cameras(intent_obj, kind, cameras)

        # Speech text lives in the custom sentence responses, not here
        response.async_set_speech_slots(
            {
                "found": kind in FOUND_KINDS,
                "kind": kind,
                "name": name,
                "empty": kind == "empty",
            }
        )
        return response
