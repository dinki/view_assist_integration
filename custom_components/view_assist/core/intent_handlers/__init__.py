"""Intent handlers that override or add to core HA intent handlers."""

from enum import StrEnum
import logging
from typing import Any, override

from homeassistant.core import State, callback
from homeassistant.helpers import entity_registry as er, intent, label_registry as lr
from homeassistant.helpers.intent import (
    Intent,
    IntentHandler,
    IntentResponse,
    IntentResponseErrorCode,
    IntentResponseTarget,
    IntentResponseType,
    MatchTargetsConstraints,
    MatchTargetsPreferences,
    MatchTargetsResult,
    async_match_targets,
)

from ...helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_entity_attribute,
    get_entity_id_from_conversation_device_id,
)
from ...typed import VAConfigEntry  # noqa: TID252

_LOGGER = logging.getLogger(__name__)


class VAIntentResponseErrorCode(StrEnum):
    """Custom error response codes for View Assist intents."""

    NOT_FOUND = "not_found"
    MULTIPLE_DOMAINS = "multiple_domains"
    UNSUPPORTED_DOMAIN = "unsupported_domain"
    HANDLER_NOT_FOUND = "handler_not_found"
    NOT_ACTIVE_LIST = "not_active_list"
    ALREADY_EXISTS = "already_exists"
    ITEM_NOT_FOUND = "item_not_found"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE_DEVICE = "incompatible_device"
    CLIMATE_OFF = "climate_off"
    OUT_OF_RANGE = "out_of_range"


class IntentOverrideHandler(IntentHandler):
    """Handler to override existing intent handlers."""

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentOverrideResponse:
        """Handle the intent."""
        raise NotImplementedError

    def get_device_info(self, intent_obj: Intent) -> IntentDeviceInfo:
        """Get the device info for the intent."""
        return IntentDeviceInfo(intent_obj)

    def get_matched_targets(
        self,
        intent_obj: Intent,
        domains: set[str] | None = None,
        features: int | None = None,
        exposed_only: bool = True,
        single_target: bool = False,
        raise_on_no_match: bool = True,
    ) -> MatchTargetsResult:
        """Extract thermostat names from the slots."""
        hass = intent_obj.hass
        slots = intent_obj.slots

        matched = False

        name: str | None = None
        if "name" in slots:
            name = slots["name"]["value"]

        area_name: str | None = None
        if "area" in slots:
            area_name = slots["area"]["value"]

        floor_name: str | None = None
        if "floor" in slots:
            floor_name = slots["floor"]["value"]

        match_constraints = MatchTargetsConstraints(
            name=name,
            area_name=area_name,
            floor_name=floor_name,
            domains=domains,
            features=features,
            assistant=intent_obj.assistant if exposed_only else None,
            single_target=single_target,
        )

        _LOGGER.debug("Match constraints: %s", match_constraints)

        match_preferences = MatchTargetsPreferences(
            area_id=slots.get("preferred_area_id", {}).get("value"),
            floor_id=slots.get("preferred_floor_id", {}).get("value"),
        )
        match_result = async_match_targets(hass, match_constraints, match_preferences)
        matched = match_result.is_match

        # If no match was found, try matching by name with entity_type if provided
        if not matched and "entity_type" in slots:
            entity_type = slots["entity_type"]["value"]
            # You can add additional logic here to handle entity_type matching if needed
            match_constraints = MatchTargetsConstraints(
                name=f"{name} {entity_type}",
                area_name=area_name,
                floor_name=floor_name,
                domains=domains,
                features=features,
                assistant=intent_obj.assistant if exposed_only else None,
                single_target=single_target,
            )
            match_preferences = MatchTargetsPreferences(
                area_id=slots.get("preferred_area_id", {}).get("value"),
                floor_id=slots.get("preferred_floor_id", {}).get("value"),
            )
            match_result = async_match_targets(
                hass, match_constraints, match_preferences
            )
            matched = match_result.is_match

        # If still no match was found, try matching by label names
        if not matched:
            label_names = [name]
            if "entity_type" in slots:
                entity_type = slots["entity_type"]["value"]
                label_names.append(f"{name} {entity_type}")

            for label_name in label_names:
                try:
                    label = lr.async_get(hass).async_get_label_by_name(label_name)
                except AttributeError:
                    label = None

                if label:
                    ent_reg = er.async_get(hass)
                    entities = [
                        entry.entity_id
                        for entry in er.async_entries_for_label(ent_reg, label.label_id)
                        if entry.domain in domains and not entry.disabled
                    ]
                    if entities:
                        match_result = MatchTargetsResult(
                            is_match=True,
                            states=[
                                hass.states.get(entity_id) for entity_id in entities
                            ],
                        )

        if not match_result.is_match and raise_on_no_match:
            raise intent.MatchFailedError(
                result=match_result, constraints=match_constraints
            )
        return match_result

    def create_override_handler_response(
        self, intent_obj: Intent, set_slots_from_intent: bool = True
    ) -> IntentOverrideResponse:
        """Create an intent override response.

        Unless specified otherwise, the speech slots will be set from the intent's slots.
        """
        response = intent_obj.create_response()
        if set_slots_from_intent:
            for slot, slot_value in intent_obj.slots.items():
                response.speech_slots[slot] = slot_value.get("value")
        return IntentOverrideResponse(response)


class IntentOverrideResponse:
    """Response class for intent overrides."""

    def __init__(
        self,
        response: IntentResponse,
    ) -> None:
        """Initialize an IntentResponse."""
        self.response = response
        self.view_data: dict[str, Any] = {}
        # Set by a handler to pass the intent on to the original handler
        self.call_original: bool = False

    @callback
    def async_set_speech(
        self,
        speech: str,
        speech_type: str = "plain",
        extra_data: Any | None = None,
    ) -> None:
        """Set speech response."""
        self.response.speech[speech_type] = {
            "speech": speech,
            "extra_data": extra_data,
        }

    @callback
    def async_set_reprompt(
        self,
        speech: str,
        speech_type: str = "plain",
        extra_data: Any | None = None,
    ) -> None:
        """Set reprompt response."""
        self.response.reprompt[speech_type] = {
            "reprompt": speech,
            "extra_data": extra_data,
        }

    @callback
    def async_set_card(
        self, title: str, content: str, card_type: str = "simple"
    ) -> None:
        """Set card response."""
        self.response.card[card_type] = {"title": title, "content": content}

    @callback
    def async_set_error(
        self,
        code: IntentResponseErrorCode | VAIntentResponseErrorCode,
        message: str | None = None,
        slots: dict[str, Any] | None = None,
    ) -> None:
        """Set response error."""
        self.response.response_type = IntentResponseType.ERROR
        self.response.error_code = code

        self.response.speech_slots = {"error": code}
        if slots:
            self.response.speech_slots.update(slots)

        if message:
            self.async_set_speech(message)

    @callback
    def async_set_results(
        self,
        success_results: list[IntentResponseTarget],
        failed_results: list[IntentResponseTarget] | None = None,
    ) -> None:
        """Set response results."""
        self.response.success_results = success_results
        self.response.failed_results = (
            failed_results if failed_results is not None else []
        )

    @callback
    def async_set_states(
        self, matched_states: list[State], unmatched_states: list[State] | None = None
    ) -> None:
        """Set matched/unmatched entity states during intent handling."""
        self.response.matched_states = matched_states
        self.response.unmatched_states = unmatched_states or []

    @callback
    def async_set_speech_slots(self, speech_slots: dict[str, Any]) -> None:
        """Set slots that will be used in the response template of the default agent."""
        if self.response.speech_slots:
            self.response.speech_slots.update(speech_slots)
        else:
            self.response.speech_slots = speech_slots

    @callback
    def async_set_response_type(self, response_type: IntentResponseType) -> None:
        """Set the response type for this response."""
        self.response.response_type = response_type

    @callback
    def async_set_view_data(self, view_data: dict[str, Any] | None) -> None:
        """Set the view data associated with this response."""
        self.view_data = view_data


class IntentDeviceInfo:
    """Class to hold device information."""

    def __init__(self, intent_obj: Intent) -> None:
        """Initialize the DeviceInfo class."""
        self.hass = intent_obj.hass
        self.conversation_device_id = intent_obj.device_id

    @property
    def entity_id(self) -> str | None:
        """Get the entity id for the device."""
        return get_entity_id_from_conversation_device_id(
            self.hass, self.conversation_device_id
        )

    @property
    def entry(self) -> VAConfigEntry | None:
        """Get the config entry for the device."""
        return get_config_entry_by_entity_id(self.hass, self.entity_id)

    @property
    def entry_id(self) -> str | None:
        """Get the entry id for the device."""
        if entry := self.entry:
            return entry.entry_id
        return None

    @property
    def entity_name(self) -> str | None:
        """Get the entity name for the device."""
        return (
            get_entity_attribute(self.hass, self.entity_id, "friendly_name")
            if self.entity_id
            else None
        )

    @property
    def area(self) -> str | None:
        """Get the area for the device."""
        return (
            get_entity_attribute(self.hass, self.entity_id, "area")
            if self.entity_id
            else None
        )

    @property
    def music_player(self) -> str | None:
        """Get the media player entity id for the device."""
        return (
            get_entity_attribute(self.hass, self.entity_id, "musicplayer_device")
            if self.entity_id
            else None
        )
