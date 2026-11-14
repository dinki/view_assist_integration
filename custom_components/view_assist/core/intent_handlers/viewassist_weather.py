"""Weather intents for View Assist.

ViewAssistWeatherCurrent, ViewAssistWeatherForecast and ViewAssistWeatherRain answer
questions about any weather.* entity. They are plain intents that supplement the
built-in HassGetWeather intent, which is left as it is.

The weather view opens for these intents through INTENT_TO_VIEW_MAPPING in const.py,
including when a request fails. The navigation manager skips devices that cannot
show views, so audio-only satellites get no view change.

Speech is written in core/custom_sentences/en/view_assist_weather.yaml. These
handlers set speech slots only.
"""

from typing import override

import voluptuous as vol

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import intent as intent_helper
from homeassistant.helpers.intent import (
    Intent,
    IntentHandler,
    IntentResponse,
    MatchTargetsConstraints,
)
from homeassistant.util import dt as dt_util

from ..weather_data import (  # noqa: TID252
    WEATHER_DOMAIN,
    async_get_forecasts,
    current_slots,
    error_slots,
    forecast_slots,
    rain_slots,
    resolve_day,
    supported_types,
)

ASSIST_ASSISTANT = "conversation"
LEADING_ARTICLES = ("the ", "my ", "a ", "an ", "our ")


def slot_value(intent_obj: Intent, name: str, *, lower: bool = True) -> str | None:
    """Return a slot value as text, or None if it is missing or empty."""
    slot = intent_obj.slots.get(name)
    if not slot:
        return None
    value = slot.get("value")
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.lower() if lower else text


def clean_name(spoken: str | None) -> str | None:
    """Remove leading articles from a spoken entity name, so "the garage" becomes "garage"."""
    if not spoken:
        return None
    name = spoken.strip()
    if name.lower().startswith(LEADING_ARTICLES):
        name = name.split(" ", 1)[1]
    return name.strip() or None


def find_weather_entity(
    hass: HomeAssistant, intent_obj: Intent, spoken: str | None
) -> State | None:
    """Find the weather entity to answer for.

    A spoken name is matched against friendly names and aliases of exposed weather
    entities. With no name, the first exposed weather entity is used, sorted by
    entity id.
    """
    assistant = intent_obj.assistant or ASSIST_ASSISTANT
    if spoken:
        constraints = MatchTargetsConstraints(
            name=spoken, domains=[WEATHER_DOMAIN], assistant=assistant
        )
        result = intent_helper.async_match_targets(hass, constraints)
        if result.is_match and result.states:
            return result.states[0]
        return None

    exposed = [
        state
        for state in sorted(
            hass.states.async_all(WEATHER_DOMAIN), key=lambda s: s.entity_id
        )
        if async_should_expose(hass, assistant, state.entity_id)
    ]
    return exposed[0] if exposed else None


def missing_entity_slots(spoken: str | None) -> dict:
    """Return error slots for a request where no weather entity was found."""
    if spoken:
        return error_slots("not_found", spoken)
    return error_slots("no_entity")


class VAWeatherCurrentIntentHandler(IntentHandler):
    """Speak the current conditions from a weather entity."""

    intent_type = "ViewAssistWeatherCurrent"
    description = """
        Speaks the current weather from a weather entity: conditions, temperature,
        humidity, wind and UV. Does not change the forecast.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {vol.Optional("weather"): str}

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Return the current conditions as speech slots."""
        hass = intent_obj.hass
        response = intent_obj.create_response()
        spoken = clean_name(slot_value(intent_obj, "weather", lower=False))

        state = find_weather_entity(hass, intent_obj, spoken)
        if state is None:
            response.async_set_speech_slots(missing_entity_slots(spoken))
            return response

        if state.state in ("unknown", "unavailable", "None", ""):
            response.async_set_speech_slots(error_slots("no_data", state.name))
            return response

        response.async_set_speech_slots(current_slots(state))
        return response


class VAWeatherForecastIntentHandler(IntentHandler):
    """Speak the forecast high, low or outlook for a day."""

    intent_type = "ViewAssistWeatherForecast"
    description = """
        Speaks the forecasted high or low temperature, or the outlook, for today,
        tonight, tomorrow, the day after tomorrow or a named weekday. Uses a weather
        entity's forecast.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("weather"): str,
            vol.Optional("measure"): str,
            vol.Optional("forecast_day"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Return the forecast as speech slots."""
        hass = intent_obj.hass
        response = intent_obj.create_response()
        spoken = clean_name(slot_value(intent_obj, "weather", lower=False))

        state = find_weather_entity(hass, intent_obj, spoken)
        if state is None:
            response.async_set_speech_slots(missing_entity_slots(spoken))
            return response

        if not supported_types(state):
            response.async_set_speech_slots(error_slots("unsupported", state.name))
            return response

        measure = slot_value(intent_obj, "measure") or "outlook"
        day_label = slot_value(intent_obj, "forecast_day") or "today"
        now = dt_util.now()
        day = resolve_day(day_label, now.date())

        forecasts = await async_get_forecasts(hass, state)
        response.async_set_speech_slots(
            forecast_slots(forecasts, measure, day_label, day, now)
        )
        return response


class VAWeatherRainIntentHandler(IntentHandler):
    """Say whether rain or snow is expected for a day or part of a day."""

    intent_type = "ViewAssistWeatherRain"
    description = """
        Says whether rain or snow is expected today, tonight, tomorrow, on a named
        weekday, or in a part of the day such as this afternoon. Uses the chance of
        precipitation from a weather entity's forecast.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("weather"): str,
            vol.Optional("day"): str,
            vol.Optional("part"): str,
            vol.Optional("precip"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Return the rain or snow answer as speech slots."""
        hass = intent_obj.hass
        response = intent_obj.create_response()
        spoken = clean_name(slot_value(intent_obj, "weather", lower=False))

        state = find_weather_entity(hass, intent_obj, spoken)
        if state is None:
            response.async_set_speech_slots(missing_entity_slots(spoken))
            return response

        if not supported_types(state):
            response.async_set_speech_slots(error_slots("unsupported", state.name))
            return response

        now = dt_util.now()
        day_label = slot_value(intent_obj, "day") or "today"
        day = resolve_day(day_label, now.date())
        part = slot_value(intent_obj, "part")
        asked = slot_value(intent_obj, "precip")

        forecasts = await async_get_forecasts(hass, state)
        response.async_set_speech_slots(
            rain_slots(forecasts, day_label, day, part, asked, now)
        )
        return response
