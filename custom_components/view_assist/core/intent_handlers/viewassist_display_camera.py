"""Display camera intent handler."""

from typing import override

import voluptuous as vol

from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.intent import Intent, IntentResponse

from ...const import DOMAIN, VAMode  # noqa: TID252
from ...devices.status import StatusManager  # noqa: TID252
from ...typed import VAEvent, VAEventType  # noqa: TID252
from . import IntentOverrideHandler


class VAViewAssistCancelCameraIntentHandler(IntentOverrideHandler):
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


class VAViewAssistDisplayCameraIntentHandler(IntentOverrideHandler):
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
        }

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Display camera through View Assist."""
        hass = intent_obj.hass
        camera = (
            intent_obj.slots["camera"]["value"]
            if "camera" in intent_obj.slots
            else None
        )
        device_info = self.get_device_info(intent_obj)

        sm = StatusManager.get(hass, device_info.entry)

        if camera:
            # Validate if valid camera is provided by enumerating all cameras and using their friendly name
            for available_camera in hass.states.async_all("camera"):
                if (
                    camera
                    and camera.lower()
                    == available_camera.attributes.get("friendly_name", "")
                    .lower()
                    .replace("camera", "")
                    .strip()
                ):
                    sm.cameras = available_camera.entity_id
                    break
        else:
            sm.cameras = None

        # Send display camera event
        async_dispatcher_send(
            hass,
            f"{DOMAIN}_{device_info.entry_id}_event",
            VAEvent(VAEventType.MODE_CHANGE, {"mode": VAMode.CAMERA}),
        )

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {
                "camera": camera,
            }
        )
        return response
