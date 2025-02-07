"""Handles entity listeners."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

_LOGGER = logging.getLogger(__name__)


class EntityListeners:
    """Class to manage entity monitors."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config_entry = config_entry

        # mic_device = self.config_entry.data["mic_device"]
        mic_device = "input_boolean.test"
        #mediaplayer_device = "media_player.viewassist_officetsv"
        mediaplayer_device = self.config_entry.data["mediaplayer_device"]
        
        # Add mic listener
        config_entry.async_on_unload(
            async_track_state_change_event(hass, mic_device, self._async_on_mic_change)
        )

        # Add media player mute listener
        config_entry.async_on_unload(
            async_track_state_change_event(hass, mediaplayer_device, self._async_on_mediaplayer_device_mute_change)
        )

    @callback
    def _async_on_mic_change(self, event: Event[EventStateChangedData]) -> None:
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        _LOGGER.info("OLD STATE: %s", old_state.state)
        _LOGGER.info("NEW STATE: %s", new_state.state)

    @callback
    def _async_on_mediaplayer_device_mute_change(self, event: Event[EventStateChangedData]) -> None:
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        _LOGGER.info("OLD STATE: %s", old_state.attributes['is_volume_muted'])
        _LOGGER.info("NEW STATE: %s", new_state.attributes['is_volume_muted'])
