"""Locate a person: show where a Home Assistant person is on a View Assist satellite.

Matches friendly names and the aliases set on the person's entity.
Overrides HassGetState, but only for locate phrases that name a person entity.
Everything else is passed on to the original Home Assistant handler.
"""

import logging
import re
from datetime import datetime
from typing import override

from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.intent import Intent
from homeassistant.util import dt as dt_util

from ...const import DOMAIN  # noqa: TID252
from . import IntentOverrideHandler, IntentOverrideResponse

_LOGGER = logging.getLogger(__name__)

# Only these command starts are locate requests. "Is Sarah home?" keeps the built-in answer.
LOCATE_PATTERN = re.compile(r"^\s*(locate|map|where is|where's)\b", re.IGNORECASE)
# Fixed for now. Becomes a master config setting later.
LOCATE_MAP_MODE = "dark"
LOCATE_VIEW = "locate"
DEFAULT_DASHBOARD = "/view-assist"
UNAVAILABLE_STATES = ("unknown", "unavailable", "None")


def _normalise(name: object) -> str:
    """Lower case and trim a name for comparison. Non-string values become text."""
    return str(name).strip().lower()


def _friendly_name(state: State) -> str:
    """Return the friendly name of a state, falling back to its name."""
    return state.attributes.get("friendly_name") or state.name


def _position_time(hass, state: State) -> datetime:
    """Return when the person's position last updated, using the tracker when there is one."""
    source = state.attributes.get("source")
    if source and (tracker := hass.states.get(source)):
        return tracker.last_updated
    return state.last_updated


def _person_names(hass, state: State) -> list[str]:
    """Return the friendly name and any entity registry aliases for a person, normalised."""
    names = [_normalise(_friendly_name(state))]
    if entry := er.async_get(hass).async_get(state.entity_id):
        for alias in entry.aliases:
            if isinstance(alias, str) and alias.strip():
                names.append(_normalise(alias))
            else:
                _LOGGER.warning(
                    "Ignoring invalid alias %r on %s", alias, state.entity_id
                )
    return names


def find_person(hass, spoken_name: str) -> State | None:
    """Return the person matching a friendly name or alias, exact matches first, then contains."""
    wanted = _normalise(spoken_name)
    if not wanted:
        return None

    exact: list[State] = []
    partial: list[State] = []
    for state in hass.states.async_all("person"):
        names = _person_names(hass, state)
        if wanted in names:
            exact.append(state)
        elif any(wanted in name for name in names):
            partial.append(state)

    candidates = exact or partial
    if not candidates:
        return None
    # If several match, use the one whose position was updated most recently
    return max(candidates, key=lambda s: _position_time(hass, s))


def _location(hass, state: State) -> tuple[bool, str, str | None]:
    """Return (away, location text, geocoded sensor entity_id) for a person."""
    tracker = state.attributes.get("source") or ""
    geocoded = (
        f"sensor.{tracker.split('.', 1)[1]}_geocoded_location"
        if "." in tracker
        else None
    )

    if state.state != "not_home":
        return False, state.state.capitalize(), geocoded

    geo_state = hass.states.get(geocoded) if geocoded else None
    if geo_state and geo_state.state not in UNAVAILABLE_STATES:
        return True, geo_state.state, geocoded
    return True, "Unknown Location", geocoded


class VALocatePersonIntentHandler(IntentOverrideHandler):
    """Show a person's location, or pass the intent on to the built-in handler."""

    intent_type = "HassGetState"
    description = """
        Overrides the built-in state query only for locate commands, such as
        'where is Sarah', that name a person. Shows the person's location on the
        locate view. Other state queries use the built-in handler.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema. The built-in slots are used as they are."""
        return None

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentOverrideResponse:
        """Locate the person, or pass the intent on."""
        hass = intent_obj.hass
        spoken_name = (intent_obj.slots.get("name") or {}).get("value") or ""
        command = intent_obj.text_input or ""

        response = self.create_override_handler_response(intent_obj)
        person = (
            find_person(hass, spoken_name) if LOCATE_PATTERN.search(command) else None
        )
        if person is None:
            response.call_original = True
            return response

        away, location, geocoded = _location(hass, person)
        tracker = person.attributes.get("source") or ""
        last_changed = _position_time(hass, person)
        seconds = max(0, int((dt_util.utcnow() - last_changed).total_seconds()))

        device = self.get_device_info(intent_obj)
        satellite = device.entity_id
        if satellite:
            await hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "locate_data": {
                        "person": person.entity_id,
                        "tracker": tracker,
                        "geocoded": geocoded or "",
                        "location_text": location,
                        "last_changed": last_changed.isoformat(),
                        "map_mode": LOCATE_MAP_MODE,
                    }
                },
                blocking=True,
                context=intent_obj.context,
                target={"entity_id": satellite},
            )

            # Navigation is skipped on audio-only devices by the navigation manager
            entry = device.entry
            if entry is not None:
                dashboard = entry.runtime_data.dashboard.dashboard or DEFAULT_DASHBOARD
                await hass.services.async_call(
                    DOMAIN,
                    "navigate",
                    {
                        "device": satellite,
                        "path": f"/{dashboard.strip('/')}/{LOCATE_VIEW}",
                    },
                    blocking=True,
                    context=intent_obj.context,
                )
        else:
            _LOGGER.debug("No satellite found for locate of %s", person.entity_id)

        # Speech text lives in the custom sentence responses, not here
        response.async_set_speech_slots(
            {
                "found": True,
                "name": _friendly_name(person),
                "away": away,
                "location": location,
                "seconds": seconds,
            }
        )
        return response
