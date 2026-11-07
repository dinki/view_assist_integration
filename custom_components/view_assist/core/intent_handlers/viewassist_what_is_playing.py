"""Intent handler that reports what is playing on a music player."""

import logging
from typing import override

import voluptuous as vol

from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.const import (
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    intent,
)

from . import IntentOverrideHandler

_LOGGER = logging.getLogger(__name__)

INTENT_WHAT_IS_PLAYING = "VAWhatIsPlaying"
_MUSIC_ASSISTANT_DOMAIN = "music_assistant"
_MUSIC_ASSISTANT_SOURCE = "Music Assistant"


def _find_area_ids(hass: HomeAssistant, area_name: str) -> set[str]:
    """Return the ids of areas whose name or alias matches area_name."""
    target = area_name.strip().casefold()
    return {
        area.id
        for area in ar.async_get(hass).async_list_areas()
        if area.name.casefold() == target
        or target in {alias.casefold() for alias in area.aliases}
    }


def _find_music_assistant_players(
    hass: HomeAssistant, area_ids: set[str]
) -> list[State]:
    """Return states of Music Assistant media players located in the given areas."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    players: list[State] = []

    for entry in ent_reg.entities.values():
        if entry.domain != MEDIA_PLAYER_DOMAIN or entry.disabled:
            continue

        # Entity area overrides device area
        entry_area_id = entry.area_id
        if entry_area_id is None and entry.device_id:
            device = dev_reg.async_get(entry.device_id)
            entry_area_id = device.area_id if device else None
        if entry_area_id not in area_ids:
            continue

        state = hass.states.get(entry.entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            continue

        attrs = state.attributes
        if (
            entry.platform == _MUSIC_ASSISTANT_DOMAIN
            or attrs.get("app_id") == _MUSIC_ASSISTANT_DOMAIN
            or attrs.get("source") == _MUSIC_ASSISTANT_SOURCE
        ):
            players.append(state)

    return players


class VAWhatIsPlayingHandler(IntentOverrideHandler):
    """Report the title/artist currently playing on a music player."""

    intent_type = INTENT_WHAT_IS_PLAYING
    description = (
        "Tells the user what song and artist is currently playing on the "
        "music player. Optionally takes an 'area' to check a specific room."
    )

    @property
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("name"): cv.string,
            vol.Optional("area"): cv.string,
        }

    @override
    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict[str, any] | None = None
    ) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        area_name: str | None = slots.get("area", {}).get("value")
        state: State | None = None

        _LOGGER.warning("VAWhatIsPlaying NEW CODE running, slots=%s", intent_obj.slots)

        if area_name:
            # Room requested: search Music Assistant players in that area
            area_ids = _find_area_ids(hass, area_name)
            players = _find_music_assistant_players(hass, area_ids) if area_ids else []
            _LOGGER.debug(
                "VAWhatIsPlaying area=%s area_ids=%s players=%s",
                area_name,
                area_ids,
                [p.entity_id for p in players],
            )
            # Prefer a player that is actually playing or paused
            state = next(
                (p for p in players if p.state in (STATE_PLAYING, STATE_PAUSED)),
                players[0] if players else None,
            )
        else:
            # No room: use this device's own music player
            device_info = self.get_device_info(intent_obj)
            state = (
                hass.states.get(device_info.music_player)
                if device_info.music_player
                else None
            )

        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.QUERY_ANSWER

        # No speech is set here. The response template in the sentence file
        # renders the speech using the state attached below.
        if state is not None:
            response.async_set_states(matched_states=[state])

        return response
