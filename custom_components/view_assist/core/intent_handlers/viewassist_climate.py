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

from enum import StrEnum
import logging
from typing import override

import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    DOMAIN as CLIMATE_DOMAIN,
    INTENT_SET_TEMPERATURE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.helpers import intent
from homeassistant.helpers.intent import Intent, IntentResponse

from ...typed import VAType  # noqa: TID252
from . import IntentOverrideHandler

_LOGGER = logging.getLogger(__name__)


class VAIntentResponseErrorCode(StrEnum):
    """Error codes for VA intent responses."""

    NO_DEVICE = "no_device"
    UNAVAILABLE = "unavailable"


class VAClimateSetTemperatureIntentHandler(IntentOverrideHandler):
    """Raise the temperature of thermostats by an amount."""

    intent_type = INTENT_SET_TEMPERATURE
    description = "Sets the target temperature of a climate device or entity"

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("temperature"): vol.Coerce(float),
            vol.Optional("temp_type"): str,
            vol.Optional("name"): str,
            vol.Optional("area"): str,
            vol.Optional("floor"): str,
        }

    @override
    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        temperature: float = slots["temperature"]["value"]
        temp_type: str | None = slots.get("temp_type", {}).get(
            "value", "set_temperature"
        )

        response = self.create_override_handler_response(intent_obj)

        match_result = self.get_matched_targets(
            intent_obj,
            domains=[CLIMATE_DOMAIN],
            features=ClimateEntityFeature.TARGET_TEMPERATURE,
        )

        for state in match_result.states:
            # Validations and error handling
            if state.state == "unavailable":
                response.async_set_error(VAIntentResponseErrorCode.UNAVAILABLE)
                return response
            if (
                state.state == HVACMode.OFF
                or state.attributes.get(ATTR_TEMPERATURE) is None
            ):
                response.async_set_error(VAIntentResponseErrorCode.CLIMATE_OFF)
                return response

            # Handle if temperature is a delta value
            if temp_type == "increase_delta":
                temperature = state.attributes.get(ATTR_TEMPERATURE) + temperature
            elif temp_type == "decrease_delta":
                temperature = state.attributes.get(ATTR_TEMPERATURE) - temperature

            if temperature < state.attributes.get(
                ATTR_MIN_TEMP, float("-inf")
            ) or temperature > state.attributes.get(ATTR_MAX_TEMP, float("inf")):
                response.async_set_error(VAIntentResponseErrorCode.OUT_OF_RANGE)
                return response

            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                service_data={ATTR_TEMPERATURE: temperature},
                target={ATTR_ENTITY_ID: state.entity_id},
                blocking=True,
            )

            response.async_set_results(
                success_results=[
                    intent.IntentResponseTarget(
                        type=intent.IntentResponseTargetType.ENTITY,
                        name=climate_state.name,
                        id=climate_state.entity_id,
                    )
                    for climate_state in match_result.states
                ]
            )
            response.async_set_states(matched_states=match_result.states)
            response.async_set_view_data(
                view_data={
                    "climate": [state.entity_id for state in match_result.states]
                }
            )
        return response


class VAShowThermostatIntentHandler(IntentOverrideHandler):
    """Show thermostats on the satellite's thermostat view."""

    intent_type = "ViewAssistShowClimate"
    description = """
        Shows a thermostat, or thermostats in an area or label, on a View Assist
        satellite. The default thermostat is used when no name is given.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("name"): str,
            vol.Optional("area"): str,
            vol.Optional("floor"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Handle the intent."""

        response = self.create_override_handler_response(intent_obj)
        device_info = self.get_device_info(intent_obj)

        # Validations
        if device_info.entry.runtime_data.core.type == VAType.AUDIO_ONLY:
            response.async_set_error(
                VAIntentResponseErrorCode.INCOMPATIBLE_DEVICE,
            )
            return response

        match_result = self.get_matched_targets(
            intent_obj,
            domains=[CLIMATE_DOMAIN],
            features=ClimateEntityFeature.TARGET_TEMPERATURE,
            raise_on_no_match=False,
        )

        if not match_result:
            response.async_set_error(
                code=VAIntentResponseErrorCode.NOT_FOUND,
            )
            return response

        response.async_set_results(
            success_results=[
                intent.IntentResponseTarget(
                    type=intent.IntentResponseTargetType.ENTITY,
                    name=climate_state.name,
                    id=climate_state.entity_id,
                )
                for climate_state in match_result.states
            ]
        )
        response.async_set_states(matched_states=match_result.states)
        response.async_set_view_data(
            view_data={
                "climate": match_result.states[0].entity_id
                if match_result.states
                else None
            }
        )
        return response
