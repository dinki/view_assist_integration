"""Intent handler that repeats the last response spoken by the calling device."""

import logging
from typing import override

from homeassistant.helpers import intent

from ...devices.status import StatusManager  # noqa: TID252
from ...helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_entity_id_from_conversation_device_id,
)
from . import IntentOverrideHandler, IntentOverrideResponse

_LOGGER = logging.getLogger(__name__)

INTENT_WHAT_DID_YOU_SAY = "VAWhatDidYouSay"


class VAWhatDidYouSayHandler(IntentOverrideHandler):
    """Repeat the last response spoken by the calling device."""

    intent_type = INTENT_WHAT_DID_YOU_SAY
    description = "Repeats the last thing the assistant said on this device."

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return None

    @override
    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> IntentOverrideResponse:
        """Handle the intent."""
        hass = intent_obj.hass

        _LOGGER.debug(
            "%s: invoked (device_id=%s, satellite_id=%s, agent_id=%s, "
            "language=%s, text=%s)",
            self.intent_type,
            intent_obj.device_id,
            getattr(intent_obj, "satellite_id", None),
            getattr(intent_obj, "conversation_agent_id", None),
            intent_obj.language,
            intent_obj.text_input,
        )

        last_response = None
        if not intent_obj.device_id:
            _LOGGER.warning(
                "%s: no device_id on intent; request did not come from a "
                "registered device (text input / conversation UI / API call?)",
                self.intent_type,
            )
        else:
            sensor_entity_id = get_entity_id_from_conversation_device_id(
                hass, intent_obj.device_id
            )
            if not sensor_entity_id:
                _LOGGER.warning(
                    "%s: no VA sensor entity found for device_id=%s "
                    "(device not linked to a View Assist entry?)",
                    self.intent_type,
                    intent_obj.device_id,
                )
            else:
                _LOGGER.debug(
                    "%s: device_id=%s -> sensor entity %s",
                    self.intent_type,
                    intent_obj.device_id,
                    sensor_entity_id,
                )
                entry = get_config_entry_by_entity_id(hass, sensor_entity_id)
                if not entry:
                    _LOGGER.warning(
                        "%s: no config entry found for entity %s",
                        self.intent_type,
                        sensor_entity_id,
                    )
                else:
                    _LOGGER.debug(
                        "%s: entity %s -> config entry %s (%s)",
                        self.intent_type,
                        sensor_entity_id,
                        entry.entry_id,
                        entry.title,
                    )
                    sm = StatusManager.get(hass, entry)
                    if not sm:
                        _LOGGER.warning(
                            "%s: no StatusManager for config entry %s (%s)",
                            self.intent_type,
                            entry.entry_id,
                            entry.title,
                        )
                    else:
                        last_response = sm.last_response
                        _LOGGER.debug(
                            "%s: StatusManager found for %s; last_response=%r",
                            self.intent_type,
                            entry.title,
                            last_response,
                        )

        response = self.create_override_handler_response(intent_obj)
        response.response_type = intent.IntentResponseType.QUERY_ANSWER

        speech_text = last_response
        if speech_text:
            word_count = len(speech_text.split())
            message_font_size = ["10vw", "8vw", "6vw", "4vw"][min(word_count // 6, 3)]

            response.async_set_speech_slots({"last_response": speech_text})

            response.async_set_view_data(
                {
                    "title": "Last Response",
                    "message": speech_text,
                    "message_font_size": message_font_size,
                }
            )
            _LOGGER.debug(
                "%s: responding with %d words, font size %s",
                self.intent_type,
                word_count,
                message_font_size,
            )
        else:
            _LOGGER.warning(
                "%s: nothing to repeat (last_response is empty/None); "
                "returning response with no speech or view data",
                self.intent_type,
            )

        return response