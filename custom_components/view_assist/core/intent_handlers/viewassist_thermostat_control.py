"""Thermostat intents for View Assist.

Intents: increase, decrease and set the temperature, and show thermostats. The show
phrases reach the show intent through the show router (viewassist_show_router.py).

A named thermostat is matched in this order, the same as cameras:
1. Exact name or alias. More than one exact match is a group.
2. Label name (group).
3. Area name or alias (group).
4. Partial name.

With no name, the exact name "thermostat" is used first, then the thermostats in the
satellite's area, then the only exposed thermostat in the house. "All" means every
exposed, available thermostat.

The satellite's thermostats attribute is a list of entity IDs, which the thermostat
view shows. Temperature rules follow the thermostat blueprint.
"""

import logging
import math
from typing import override

import voluptuous as vol

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.intent import Intent, IntentResponse

from ...const import DOMAIN  # noqa: TID252
from ...devices.status import StatusManager  # noqa: TID252
from . import IntentDeviceInfo, IntentOverrideHandler

_LOGGER = logging.getLogger(__name__)

THERMOSTAT_WORDS = ("thermostat", "thermostats")
ALL_THERMOSTATS = "all"
ASSIST_ASSISTANT = "conversation"
UNAVAILABLE_STATES = ("unknown", "unavailable", "None")
# Words that mean no name was given, so the default thermostat is used
IGNORED_TARGETS = ("", "thermostat", "thermostats", "temperature", "temperatures")
LEADING_ARTICLES = ("the", "my", "a", "an", "our")
# Modes that accept each change, as in the blueprint
CHANGE_MODES = ("heat", "cool", "heat_cool")
SET_MODES = ("heat", "cool")
NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
}  # fmt: skip


def normalise(value: object) -> str:
    """Return the value as lower case text without surrounding space."""
    return str(value).strip().lower()


def clean_target(spoken: object) -> str:
    """Return a spoken name without leading articles, such as "the" or "my".

    The sentence wildcard can capture "the" in "raise the thermostat", so it is
    removed before matching.
    """
    words = normalise(spoken).split()
    while words and words[0] in LEADING_ARTICLES:
        words.pop(0)
    return " ".join(words)


def strip_thermostat_word(name: object) -> tuple[str, bool]:
    """Return the name without a trailing thermostat word, and whether one was there."""
    words = normalise(name).split()
    had_word = bool(words) and words[-1] in THERMOSTAT_WORDS
    if had_word:
        words = words[:-1]
    return " ".join(words), had_word


def friendly_name(hass: HomeAssistant, entity_id: str) -> str:
    """Return the friendly name of an entity, falling back to its entity_id."""
    state = hass.states.get(entity_id)
    if state is None:
        return entity_id
    return str(state.attributes.get("friendly_name") or state.name)


def exposed_thermostats(hass: HomeAssistant) -> list[State]:
    """Return climate states exposed to Assist and currently available."""
    return [
        state
        for state in hass.states.async_all("climate")
        if state.state not in UNAVAILABLE_STATES
        and async_should_expose(hass, ASSIST_ASSISTANT, state.entity_id)
    ]


def raw_names(hass: HomeAssistant, state: State) -> set[str]:
    """Return the full friendly name and Assist aliases of a thermostat, lower case."""
    names = {normalise(state.attributes.get("friendly_name") or state.name)}
    entry = er.async_get(hass).async_get(state.entity_id)
    for alias in getattr(entry, "aliases", None) or ():
        if isinstance(alias, str) and alias.strip():
            names.add(normalise(alias))
    return names


def short_names(hass: HomeAssistant, state: State) -> set[str]:
    """Return the names of a thermostat without its thermostat word."""
    names = {strip_thermostat_word(name)[0] for name in raw_names(hass, state)}
    names.discard("")
    return names


def entity_area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the area of an entity: its own area, or the area of its device."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
        return device.area_id
    return None


def area_id_named(hass: HomeAssistant, wanted: str) -> str | None:
    """Return the ID of the area with the name or an alias wanted, or None."""
    for area in ar.async_get(hass).async_list_areas():
        names = {normalise(area.name)}
        for alias in getattr(area, "aliases", None) or ():
            if isinstance(alias, str) and alias.strip():
                names.add(normalise(alias))
        if wanted in names:
            return area.id
    return None


def label_entity_ids(hass: HomeAssistant, wanted: str) -> set[str] | None:
    """Return the entity IDs with the label named wanted, or None if no label has it."""
    label_id = next(
        (
            label.label_id
            for label in lr.async_get(hass).async_list_labels()
            if strip_thermostat_word(label.name)[0] == wanted
        ),
        None,
    )
    if label_id is None:
        return None
    return {
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if label_id in (entry.labels or ())
    }


def group_result(ids: list[str], name: str) -> tuple[str, str, list[str]]:
    """Return a result for a list of thermostats: single, group or empty."""
    if not ids:
        return "empty", name, []
    return ("single" if len(ids) == 1 else "group"), name, ids


def resolve_thermostat(hass: HomeAssistant, spoken: object) -> tuple[str, str, list[str]]:
    """Return (kind, speech name, entity IDs) for a spoken name.

    Kind is all, single, group, empty or none. Empty means a label or area matched
    but has no exposed thermostats.
    """
    wanted, _ = strip_thermostat_word(spoken)
    thermostats = exposed_thermostats(hass)

    if wanted == ALL_THERMOSTATS:
        ids = [state.entity_id for state in thermostats]
        return ("all", "", ids) if ids else ("empty", "", [])
    if not wanted:
        return "none", "", []

    exact = [s.entity_id for s in thermostats if wanted in short_names(hass, s)]
    if exact:
        return group_result(exact, wanted)

    labelled = label_entity_ids(hass, wanted)
    if labelled is not None:
        ids = [s.entity_id for s in thermostats if s.entity_id in labelled]
        return group_result(ids, wanted)

    area_id = area_id_named(hass, wanted)
    if area_id is not None:
        ids = [
            s.entity_id
            for s in thermostats
            if entity_area_id(hass, s.entity_id) == area_id
        ]
        return group_result(ids, wanted)

    partial = [
        s.entity_id
        for s in thermostats
        if any(wanted in name for name in short_names(hass, s))
    ]
    if partial:
        return group_result(partial, wanted)
    return "none", wanted, []


def default_thermostats(
    hass: HomeAssistant, satellite: str | None
) -> tuple[str, str, list[str]]:
    """Return the thermostats used when no name is given.

    Order: the exact name "thermostat", then thermostats in the satellite's area,
    then the only exposed thermostat in the house.
    """
    thermostats = exposed_thermostats(hass)

    exact = [
        s.entity_id for s in thermostats if "thermostat" in raw_names(hass, s)
    ]
    if exact:
        return group_result(exact, "")

    area_id = entity_area_id(hass, satellite) if satellite else None
    if area_id is not None:
        in_room = [
            s.entity_id
            for s in thermostats
            if entity_area_id(hass, s.entity_id) == area_id
        ]
        if in_room:
            area = ar.async_get(hass).async_get_area(area_id)
            return group_result(in_room, area.name if area else "")

    if len(thermostats) == 1:
        return "single", "", [thermostats[0].entity_id]
    return "none", "", []


def find_thermostats(
    hass: HomeAssistant, satellite: str | None, spoken: object
) -> tuple[str, str, list[str]]:
    """Return the thermostats for a spoken name, or the default ones if none is named."""
    wanted = clean_target(spoken)
    if wanted in IGNORED_TARGETS:
        return default_thermostats(hass, satellite)
    return resolve_thermostat(hass, wanted)


def show_thermostats(hass: HomeAssistant, device_info: IntentDeviceInfo, entity_ids: list[str]) -> None:
    """Set the thermostats on the satellite status, for the thermostat view to show."""
    if device_info.entry is None:
        return
    StatusManager.get(hass, device_info.entry).thermostats = entity_ids


async def async_show_thermostats(
    intent_obj: Intent, device_info: IntentDeviceInfo, entity_ids: list[str]
) -> None:
    """Show thermostats on the satellite.

    The single thermostat view reads the satellite's climate attribute, so it gets the
    first thermostat through set_state, the same way the list view is set. The
    thermostats list is also kept on the satellite for the multi-thermostat view.
    """
    show_thermostats(intent_obj.hass, device_info, entity_ids)
    if not device_info.entity_id:
        return
    await intent_obj.hass.services.async_call(
        DOMAIN,
        "set_state",
        {"climate": entity_ids[0] if entity_ids else ""},
        blocking=True,
        context=intent_obj.context,
        target={"entity_id": device_info.entity_id},
    )


def parse_amount(value: object) -> float | None:
    """Return the spoken amount as a number, or None if it is not a number."""
    text = normalise(value).replace(",", ".")
    if text in NUMBER_WORDS:
        return float(NUMBER_WORDS[text])
    try:
        amount = float(text)
    except ValueError:
        return None
    return amount if math.isfinite(amount) else None


def display_amount(amount: float) -> int | float:
    """Return a whole number as an int so speech says 2, not 2.0."""
    return int(amount) if amount.is_integer() else amount


def as_number(value: object) -> float | None:
    """Return a state attribute as a number, or None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def plan_change(
    state: State, direction: str, amount: float
) -> tuple[dict[str, float] | None, str | None]:
    """Return (service data, None) for a change, or (None, reason) when it cannot be made.

    Reasons match the blueprint responses: unavailable, off, mode or range.
    """
    mode = state.state
    if mode in UNAVAILABLE_STATES:
        return None, "unavailable"
    if mode == "off":
        return None, "off"
    supported = SET_MODES if direction == "set" else CHANGE_MODES
    if mode not in supported:
        return None, "mode"

    minimum = as_number(state.attributes.get("min_temp"))
    maximum = as_number(state.attributes.get("max_temp"))
    if minimum is None or maximum is None:
        return None, "range"
    sign = -1 if direction == "decrease" else 1

    if mode == "heat_cool" and direction != "set":
        low = as_number(state.attributes.get("target_temp_low"))
        high = as_number(state.attributes.get("target_temp_high"))
        if low is None or high is None:
            return None, "mode"
        new_low = round(low + sign * amount, 1)
        new_high = round(high + sign * amount, 1)
        if not (minimum <= new_low <= maximum and minimum <= new_high <= maximum):
            return None, "range"
        return {"target_temp_low": new_low, "target_temp_high": new_high}, None

    current = as_number(state.attributes.get("temperature"))
    if current is None:
        return None, "mode"
    new = amount if direction == "set" else current + sign * amount
    new = round(new, 1)
    if not minimum <= new <= maximum:
        return None, "range"
    return {"temperature": new}, None


async def async_change_temperature(
    intent_obj: Intent, device_info: IntentDeviceInfo, direction: str
) -> IntentResponse:
    """Change the temperature of the named thermostats, or the default ones."""
    hass = intent_obj.hass
    response = intent_obj.create_response()

    amount = (
        parse_amount(intent_obj.slots["amount"]["value"])
        if "amount" in intent_obj.slots
        else None
    )
    if amount is None:
        response.async_set_speech_slots({"error": "amount"})
        return response
    if direction != "set" and amount <= 0:
        response.async_set_speech_slots({"error": "range"})
        return response

    spoken = (
        intent_obj.slots["thermostat"]["value"]
        if "thermostat" in intent_obj.slots
        else ""
    )
    kind, name, entity_ids = find_thermostats(hass, device_info.entity_id, spoken)
    if kind == "none":
        response.async_set_speech_slots(
            {"error": "not_found", "name": clean_target(spoken) or "thermostat"}
        )
        return response
    if kind == "empty":
        response.async_set_speech_slots({"error": "empty", "name": name})
        return response

    changed: list[str] = []
    failed_names: list[str] = []
    reasons: list[str] = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        data, reason = (
            plan_change(state, direction, amount)
            if state is not None
            else (None, "unavailable")
        )
        if reason is not None or data is None:
            failed_names.append(friendly_name(hass, entity_id))
            reasons.append(reason or "unavailable")
            continue
        try:
            await hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": entity_id, **data},
                blocking=True,
                context=intent_obj.context,
            )
        except HomeAssistantError:
            _LOGGER.warning("Could not set temperature on %s", entity_id)
            failed_names.append(friendly_name(hass, entity_id))
            reasons.append("unavailable")
            continue
        changed.append(entity_id)

    if changed:
        await async_show_thermostats(intent_obj, device_info, entity_ids)

    response.async_set_speech_slots(
        {
            "error": "" if changed else (reasons[0] if reasons else "empty"),
            "name": name,
            "group": len(entity_ids) > 1,
            "total": len(entity_ids),
            "changed": len(changed),
            "failed_names": failed_names,
            "amount": display_amount(amount),
        }
    )
    return response


class VAIncreaseThermostatIntentHandler(IntentOverrideHandler):
    """Raise the temperature of thermostats by an amount."""

    intent_type = "ViewAssistIncreaseThermostat"
    description = """
        Raises the temperature set point of a thermostat, or of thermostats in an area
        or label, by an amount. The default thermostat is used when no name is given.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("thermostat"): str,
            vol.Required("amount"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Raise the temperature."""
        return await async_change_temperature(
            intent_obj, self.get_device_info(intent_obj), "increase"
        )


class VADecreaseThermostatIntentHandler(IntentOverrideHandler):
    """Lower the temperature of thermostats by an amount."""

    intent_type = "ViewAssistDecreaseThermostat"
    description = """
        Lowers the temperature set point of a thermostat, or of thermostats in an area
        or label, by an amount. The default thermostat is used when no name is given.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("thermostat"): str,
            vol.Required("amount"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Lower the temperature."""
        return await async_change_temperature(
            intent_obj, self.get_device_info(intent_obj), "decrease"
        )


class VASetThermostatIntentHandler(IntentOverrideHandler):
    """Set the temperature of thermostats to an amount."""

    intent_type = "ViewAssistSetThermostat"
    description = """
        Sets the temperature set point of a thermostat, or of thermostats in an area
        or label, to an amount. The default thermostat is used when no name is given.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("thermostat"): str,
            vol.Required("amount"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Set the temperature."""
        return await async_change_temperature(
            intent_obj, self.get_device_info(intent_obj), "set"
        )


class VAShowThermostatIntentHandler(IntentOverrideHandler):
    """Show thermostats on the satellite's thermostat view."""

    intent_type = "ViewAssistShowThermostat"
    description = """
        Shows a thermostat, or thermostats in an area or label, on a View Assist
        satellite. The default thermostat is used when no name is given.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {vol.Optional("thermostat"): str}

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Set the thermostats to show."""
        hass = intent_obj.hass
        device_info = self.get_device_info(intent_obj)
        response = intent_obj.create_response()
        spoken = (
            intent_obj.slots["thermostat"]["value"]
            if "thermostat" in intent_obj.slots
            else ""
        )

        if device_info.entry is None:
            response.async_set_speech_slots({"error": "device"})
            return response

        kind, name, entity_ids = find_thermostats(hass, device_info.entity_id, spoken)
        if kind in ("none", "empty"):
            response.async_set_speech_slots(
                {
                    "error": "not_found" if kind == "none" else "empty",
                    "name": name or clean_target(spoken) or "thermostat",
                }
            )
            return response

        await async_show_thermostats(intent_obj, device_info, entity_ids)
        response.async_set_speech_slots(
            {
                "error": "",
                "name": name,
                "group": len(entity_ids) > 1,
                "total": len(entity_ids),
            }
        )
        return response
