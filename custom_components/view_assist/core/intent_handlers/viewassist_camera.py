"""Display camera intent handler.

Names are matched in this order, using only cameras exposed to Assist:
1. Exact camera friendly name or alias. More than one exact match is a group.
2. Label name (group).
3. Area name or alias (group), only when the word cameras is said.
4. Partial camera name, only when the camera word is said.
"all" means every exposed, available camera.

The satellite's cameras attribute is set to a list of entity IDs, which the camera
card shows. The list intent uses the same matching, so phrases such as
"show kitchen cameras" work whichever intent hears them. When a phrase is not a
camera but is a list, this handler hands it to the list intent.
"""

import logging
from typing import override

import voluptuous as vol

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.intent import Intent, IntentResponse, IntentResponseErrorCode

from ...const import DOMAIN, VAMode  # noqa: TID252
from ...typed import VAEvent, VAEventType  # noqa: TID252
from . import IntentOverrideHandler

_LOGGER = logging.getLogger(__name__)


class VACancelCameraIntentHandler(IntentOverrideHandler):
    """Stop camera through View Assist."""

    intent_type = "ViewAssistCancelCamera"
    description = """
        Cancel camera through a View Assist satellite via voice command.
    """

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Cancel camera through View Assist."""
        hass = intent_obj.hass
        device_info = self.get_device_info(intent_obj)

        # Send display camera event
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_{device_info.entry_id}_event",
            VAEvent(VAEventType.MODE_CHANGE, {"mode": VAMode.NORMAL}),
        )

        return intent_obj.create_response()


class VADisplayCameraIntentHandler(IntentOverrideHandler):
    """Display camera through View Assist."""

    intent_type = "ViewAssistDisplayCamera"
    description = """
        Display camera through a View Assist satellite via voice command.
    """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("camera"): str,
            vol.Optional("area"): str,
            vol.Required("entity_type"): str,
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Display camera through View Assist."""
        hass = intent_obj.hass
        camera = intent_obj.slots.get("camera", {}).get("value")

        response = self.create_override_handler_response(intent_obj)

        if camera:
            # Add name to slots.  Does not request name in slots as that will try and validate before reaching here
            # which is not wanted
            intent_obj.slots["name"] = camera

        match_result = self.get_matched_targets(
            intent_obj, domains={CAMERA_DOMAIN}, raise_on_no_match=False
        )
        if not match_result.is_match:
            response.async_set_error(
                code=IntentResponseErrorCode.NOT_FOUND,
            )
            return response

        cameras = [state.entity_id for state in match_result.states]
        total_number_of_cameras = len(cameras)

        kind = (
            "single"
            if len(cameras) == 1
            else "all"
            if len(cameras) == total_number_of_cameras
            else "group"
            if len(cameras) > 1
            else "none"
        )

        if cameras:
            # Send camera mode event to keep on camera screen until cancelled
            device_info = self.get_device_info(intent_obj)
            async_dispatcher_send(
                hass,
                f"{DOMAIN}_{device_info.entry_id}_event",
                VAEvent(VAEventType.MODE_CHANGE, {"mode": VAMode.CAMERA}),
            )

        response.async_set_speech_slots({"kind": kind})
        response.async_set_view_data({"cameras": cameras})
        return response
