"""Say the names of the todo lists. Does not change the view."""

from typing import override

from homeassistant.helpers.intent import Intent, IntentResponse

from . import IntentOverrideHandler
from .viewassist_list_show import friendly_name, todo_entities


class VAListNamesIntentHandler(IntentOverrideHandler):
    """Speak the friendly names of all todo lists."""

    intent_type = "ViewAssistListNames"
    description = """
        Speaks the names of all todo lists in Home Assistant. Does not change the view.
        """

    @property
    @override
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return None

    @override
    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Return the list names as speech slots."""
        hass = intent_obj.hass
        names = sorted(friendly_name(hass, eid) for eid in todo_entities(hass))
        response = intent_obj.create_response()
        response.async_set_speech_slots({"status": "ok" if names else "empty", "count": len(names), "names": names})
        return response
