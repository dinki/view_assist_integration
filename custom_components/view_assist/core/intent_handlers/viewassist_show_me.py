"""Intent handler for the 'show me' command related to things."""

from dataclasses import dataclass
import logging
from typing import override

import voluptuous as vol

from homeassistant.components import camera, climate, todo
from homeassistant.helpers import intent
from homeassistant.helpers.intent import Intent, IntentResponse

from . import IntentOverrideHandler, VAIntentResponseErrorCode

_LOGGER = logging.getLogger(__name__)


@dataclass
class DomainIntentDisplayHandlerInfo:
    """Handler for displaying items based on the device domain."""

    domain: str
    intent: str


DISPLAY_HANDLERS: list[DomainIntentDisplayHandlerInfo] = [
    DomainIntentDisplayHandlerInfo(domain=todo.DOMAIN, intent="ViewAssistShowList"),
    DomainIntentDisplayHandlerInfo(
        domain=camera.DOMAIN, intent="ViewAssistDisplayCamera"
    ),
    DomainIntentDisplayHandlerInfo(
        domain=climate.DOMAIN, intent="ViewAssistShowClimate"
    ),
]


class VAShowMeIntentHandler(IntentOverrideHandler):
    """Display the requested item or items on the device."""

    intent_type = "ViewAssistShowMe"
    description = """
        Display the requested item or items on the device.
        It calls the relevant handler based on the device domain and returns the relevant response.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            # Uses device and not name as HA tries to validate name and labels will not work correctly
            vol.Required("device"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Handle the 'show me' intent."""

        hass = intent_obj.hass
        self.async_validate_slots(intent_obj.slots)

        intent_obj.slots["name"] = intent_obj.slots["device"]

        response = self.create_override_handler_response(intent_obj)

        domains = {handler.domain for handler in DISPLAY_HANDLERS}

        match_result = self.get_matched_targets(
            intent_obj, domains=domains, raise_on_no_match=False
        )
        if not match_result.is_match:
            response.async_set_error(
                code=VAIntentResponseErrorCode.NOT_FOUND,
            )
            return response

        # Decide which domain handler to call based on the matched targets
        result_domains = [target.domain for target in match_result.states]
        result_domains = set(result_domains)

        if len(result_domains) > 1:
            # Unclear which domain to target as results contain multiple domains
            response.async_set_error(
                code=VAIntentResponseErrorCode.MULTIPLE_DOMAINS,
            )
            return response

        # Single domain matched, call the relevant handler
        single_domain = result_domains.pop()
        handler_info = next(
            (
                handler
                for handler in DISPLAY_HANDLERS
                if handler.domain == single_domain
            ),
            None,
        )
        if not handler_info:
            response.async_set_error(
                code=VAIntentResponseErrorCode.UNSUPPORTED_DOMAIN,
            )
            return response

        handler = hass.data.get(intent.DATA_KEY, {}).get(handler_info.intent)
        if hasattr(handler, "async_handle"):
            intent_obj.intent_type = handler_info.intent
            return await handler.async_handle(intent_obj)

        response.async_set_error(
            code=VAIntentResponseErrorCode.HANDLER_NOT_FOUND,
        )

        return response
