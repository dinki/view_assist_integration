"""Handles entity listeners.

Update this to a generic Entity State listener for all types of entities.
Pass in the entity id, the event type and an optional callback function to handle the event.

"""

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from ..const import DEVICES, DOMAIN  # noqa: TID252
from ..helpers import get_mute_switch_entity_id  # noqa: TID252
from ..typed import VAConfigEntry, VAEvent, VAEventType  # noqa: TID252
from .base import DeviceModule

_LOGGER = logging.getLogger(__name__)


class EntityListeners(DeviceModule):
    """Class to manage entity monitors."""

    _dependencies = ["StatusManager"]

    @classmethod
    def get(cls, hass: HomeAssistant, config: VAConfigEntry) -> EntityListeners | None:
        """Get the instance for a config entry."""
        try:
            return hass.data[DOMAIN][DEVICES][config.entry_id][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the entity listeners."""
        super().__init__(hass, config)
        self._rt = self._config.runtime_data
        self.listeners: list[EntityListeners] = []

    async def async_setup(self) -> bool:
        """Load module."""
        # Add assist/mic state listener
        if assist_entity_id := self._config.runtime_data.core.mic_device:
            _LOGGER.debug("Listening for assist entity %s", assist_entity_id)
            self.listeners.append(
                EntityStateChangeHandler(
                    self._hass,
                    self._config,
                    assist_entity_id,
                    VAEventType.ASSIST_STATE_CHANGE,
                )
            )

        # Add mic listener
        if mute_switch := get_mute_switch_entity_id(
            self._hass, self._rt.core.mic_device
        ):
            _LOGGER.debug("Listening for mute switch %s", mute_switch)
            self.listeners.append(
                EntityStateChangeHandler(
                    self._hass,
                    self._config,
                    mute_switch,
                    VAEventType.MICROPHONE_STATE_CHANGE,
                )
            )

        # Add music media player listener
        if musicplayer_device := self._rt.core.musicplayer_device:
            _LOGGER.debug("Listening for music player device %s", musicplayer_device)
            self.listeners.append(
                EntityStateChangeHandler(
                    self._hass,
                    self._config,
                    musicplayer_device,
                    VAEventType.MUSIC_PLAYER_STATE_CHANGE,
                )
            )

        return True

    async def async_unload(self) -> bool:
        """Stop the EntityListeners."""
        for listener in self.listeners:
            listener.unregister()
        return True


class EntityStateChangeHandler:
    """Class to manage entity state change listeners for a second type of entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: VAConfigEntry,
        entity_id: str,
        event_type: VAEventType,
        payload_formatter: str | None = None,
    ) -> None:
        """Initialise."""
        self._hass = hass
        self._config = config
        self.entity_id = entity_id
        self.event_type = event_type
        self.payload_formatter = payload_formatter
        self._unregister: Callable[[], None] | None = None

        self._add_entity_state_listener()

    def unregister(self) -> None:
        """Unregister the state change listener for the entity."""
        if self._unregister:
            self._unregister()
            self._unregister = None

    def _add_entity_state_listener(self) -> None:
        """Add a state listener for an entity."""

        # Call listener handler with current state
        if state := self._hass.states.get(self.entity_id):
            _LOGGER.debug("Setting initial state for %s", self.entity_id)
            self._on_state_change(
                Event[EventStateChangedData](
                    event_type="initial_state",
                    data=EventStateChangedData(
                        entity_id=self.entity_id,
                        new_state=state,
                    ),
                    context=Context(id="initial_state"),
                )
            )

        # Add listener
        self._unregister = async_track_state_change_event(
            self._hass, self.entity_id, self._on_state_change
        )

    def _validate_event(self, event: Event[EventStateChangedData]) -> bool:
        """Validate event."""
        if not event.data.get("new_state"):
            # If not new state some weird error, so ignore it
            return False
        if not event.data.get("old_state"):
            # Initial state has no old state, so always process it
            return True
        if (
            event.data.get("old_state")
            and event.data["old_state"].state == event.data["new_state"].state
        ):
            # If not change to state, ignore
            return False
        return True

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle state change events for the entity."""
        if not self._validate_event(event):
            return

        new_state = event.data["new_state"]

        if self.payload_formatter and hasattr(self, self.payload_formatter):
            payload = getattr(self, self.payload_formatter)(new_state)
        else:
            payload = {"state": new_state.state, "attributes": new_state.attributes}

        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self._config.entry_id}_event",
            VAEvent(
                self.event_type,
                payload,
            ),
        )

    def _get_intent_sensor_update_payload(self, state: State) -> dict[str, Any]:
        """Get the payload for an intent sensor update based on the state."""
        return {
            "response": state.state,
        }
