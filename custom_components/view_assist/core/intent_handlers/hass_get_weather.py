"""Weather intent - overrides Home Assistant's built-in weather intent (HassGetWeather)."""

from typing import override

from homeassistant.const import CONF_TYPE
from homeassistant.helpers import intent

from ...const import DOMAIN  # noqa: TID252
from ...typed import DISPLAY_DEVICE_TYPES  # noqa: TID252
from ...helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_entity_id_from_conversation_device_id,
    get_master_config_entry,
)

# Dashboard path to navigate a display-capable satellite to after answering.
# Change this if your weather view lives at a different path.
WEATHER_VIEW_PATH = "/view-assist/weather"


class VAGetWeatherIntentHandler(intent.IntentHandler):
    """Report the current weather through a View Assist satellite.

    Registered as "HassGetWeather", the same intent_type Home Assistant's
    core `weather` integration already uses (see
    homeassistant.components.weather.const.INTENT_GET_WEATHER), so this
    overrides it the same way other handlers in this package override
    HassStartTimer, HassBroadcast, etc.
    """

    intent_type = "HassGetWeather"
    description = """
        Get the current weather conditions and temperature. Call this tool for any question about the weather right now, no location or extra details needed.
    """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {}

    @override
    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
        """Report the current weather."""
        hass = intent_obj.hass

        # device_id is not always populated when this intent is invoked via
        # an LLM tool call (as opposed to local sentence matching), so fall
        # back to satellite_id, which is the assist_satellite entity itself.
        entity_id = get_entity_id_from_conversation_device_id(
            hass, intent_obj.device_id
        ) or intent_obj.satellite_id

        # Weather entity configured in Master Configuration -> Default Options.
        master_entry = get_master_config_entry(hass)
        weather_entity = master_entry.runtime_data.default.weather_entity

        weather_state = hass.states.get(weather_entity)
        condition = weather_state.state if weather_state else None
        temperature = (
            weather_state.attributes.get("temperature") if weather_state else None
        )

        if entity_id:
            device_entry = get_config_entry_by_entity_id(hass, entity_id)
            has_display = (
                device_entry is not None
                and device_entry.data.get(CONF_TYPE) in DISPLAY_DEVICE_TYPES
            )

            await hass.services.async_call(
                DOMAIN,
                "set_state",
                {"last_said": f"{condition}, {temperature}"},
                blocking=True,
                context=intent_obj.context,
                target={"entity_id": entity_id},
            )

            # Skip navigation on audio-only satellites - they have no screen.
            if has_display:
                await hass.services.async_call(
                    DOMAIN,
                    "navigate",
                    {"path": WEATHER_VIEW_PATH},
                    blocking=True,
                    context=intent_obj.context,
                    target={"entity_id": entity_id},
                )

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {
                "condition": condition,
                "temperature": temperature,
            }
        )
        return response
