"""Assist Satellite intents."""

from typing import Final, override

import voluptuous as vol

from homeassistant.components.assist_satellite import (
    DOMAIN as ASSIST_SATELLITE_DOMAIN,
    AssistSatelliteEntityFeature,
)
from homeassistant.helpers import area_registry as ar, entity_registry as er, intent

EXCLUDED_DOMAINS: Final[set[str]] = {"voip"}


class VABroadcastIntentHandler(intent.IntentHandler):
    """Broadcast a message."""

    intent_type = intent.INTENT_BROADCAST
    description = """
        Broadcast a message through the home or in a specified area.
        Provide the area in the area slot if you want to broadcast to a specific area.
        If no area is specified, the message will be broadcast to all areas.
    """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required("message"): str,
            vol.Optional("area"): str,
        }

    @override
    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
        """Broadcast a message."""
        hass = intent_obj.hass
        ent_reg = er.async_get(hass)

        # Get area_id for area name if provided
        area_id = None
        if area_name := intent_obj.slots.get("area", {}).get("value"):
            area_registry = ar.async_get(hass)
            area_entry = area_registry.async_get_area_by_name(area_name)
            if not area_entry:
                response = intent_obj.create_response()
                # This needs to be set as an error response so that the conversation agent can handle it as an error
                response.async_set_speech(
                    f"Broadcast failed. I could not find the area named {area_name}"
                )
                return response

            area_id = area_entry.id

        # Find all assist satellite entities that are not the one invoking the intent
        entities: dict[str, er.RegistryEntry] = {}
        for entity in hass.states.async_entity_ids(ASSIST_SATELLITE_DOMAIN):
            entry = ent_reg.async_get(entity)
            if (
                (entry is None)
                or (
                    # Area id does not match
                    area_id and (entry.area_id != area_id)
                )
                or (
                    # Supports announce
                    not (
                        entry.supported_features & AssistSatelliteEntityFeature.ANNOUNCE
                    )
                )
                # Not the invoking device
                or (intent_obj.device_id and (entry.device_id == intent_obj.device_id))
            ):
                # Skip satellite
                continue

            # Check domain of config entry against excluded domains
            if (
                entry.config_entry_id
                and (
                    config_entry := hass.config_entries.async_get_entry(
                        entry.config_entry_id
                    )
                )
                and (config_entry.domain in EXCLUDED_DOMAINS)
            ):
                continue

            entities[entity] = entry

        await hass.services.async_call(
            ASSIST_SATELLITE_DOMAIN,
            "announce",
            {"message": intent_obj.slots["message"]["value"]},
            blocking=True,
            context=intent_obj.context,
            target={"entity_id": list(entities)},
        )

        response = intent_obj.create_response()
        response.async_set_results(
            success_results=[
                intent.IntentResponseTarget(
                    type=intent.IntentResponseTargetType.ENTITY,
                    id=entity,
                    name=state.name if (state := hass.states.get(entity)) else entity,
                )
                for entity in entities
            ]
        )
        response.async_set_speech_slots(
            {
                "area": intent_obj.slots.get("area", {}).get("value"),
                "message": intent_obj.slots["message"]["value"],
            }
        )
        return response
