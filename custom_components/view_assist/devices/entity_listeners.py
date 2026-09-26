"""Handles entity listeners.

Update this to a generic Entity State listener for all types of entities.
Pass in the entity id, the event type and an optional callback function to handle the event.

"""

import asyncio  # noqa: I001
from collections.abc import Callable
import logging

from homeassistant.components.media_player import MediaPlayerState
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

from ..const import (  # noqa: TID252
    DEVICES,
    DOMAIN,
    VAMode,
)
from ..helpers import (  # noqa: TID252
    get_mute_switch_entity_id,
    get_sensor_entity_from_instance,
)
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
        handler: Callable[[Event[EventStateChangedData]], None] | None = None,
    ) -> None:
        """Initialise."""
        self._hass = hass
        self._config = config
        self.entity_id = entity_id
        self.event_type = event_type
        self.handler = handler
        self._unregister: Callable[[], None] | None = None

        self._add_entity_state_listener()

    def unregister(self) -> None:
        """Unregister the state change listener for the entity."""
        if self._unregister:
            self._unregister()
            self._unregister = None

    def _add_entity_state_listener(self) -> None:
        """Add a state listener for an entity."""

        listener = self.handler or self._on_state_change

        # Call listener handler with current state
        if state := self._hass.states.get(self.entity_id):
            _LOGGER.debug("Setting initial state for %s", self.entity_id)
            listener(
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
            self._hass, self.entity_id, listener
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

        _LOGGER.debug("State changed for %s: %s", self.entity_id, new_state.state)
        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self._config.entry_id}_event",
            VAEvent(
                self.event_type,
                {"state": new_state.state, "attributes": new_state.attributes},
            ),
        )


class EntityStateChangedHandler2:
    """Class to manage entity state change listeners."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self._hass = hass
        self._config = config
        self.entity_id: str | None = None

        # Music mode auto-switching configuration
        self.music_mode_auto = config.runtime_data.default.music_mode_auto
        self.music_mode_timeout = config.runtime_data.default.music_mode_timeout
        self.music_timeout_task: asyncio.Task | None = None

    def _should_monitor_music_player(self) -> bool:
        """Check if music player monitoring should be enabled."""
        if not self._config.runtime_data.core.musicplayer_device:
            return False

        # Only monitor if at least one feature is enabled
        if self.music_mode_auto == "on" and self.music_mode_timeout > 0:
            return True

        return False

    def _is_music_content(self, state_obj: State) -> bool:
        """Check if the media content type is an audio entertainment type."""
        media_content_type = state_obj.attributes.get("media_content_type")

        allowed_types = (
            "music",
            "podcast",
            "episode",
            "track",
            "album",
            "playlist",
            "artist",
            "composer",
            "contributing_artist",
            "channel",
            "channels",
        )

        return media_content_type in allowed_types

    @callback
    def _async_on_musicplayer_entity_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle music player state changes for auto mode switching."""
        if not self._validate_event(event):
            return

        new_state_obj = event.data.get("new_state")

        state = new_state_obj.state
        payload = {
            "entity_id": new_state_obj.entity_id,
            "state": state,
            "media_content_type": new_state_obj.attributes.get("media_content_type"),
            "artist": new_state_obj.attributes.get("media_artist"),
            "album": new_state_obj.attributes.get("media_album_name"),
            "track": new_state_obj.attributes.get("media_title"),
        }

        _LOGGER.debug("Music player state change: %s", payload)

        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self._config.entry_id}_event",
            VAEvent(
                VAEventType.MUSIC_PLAYER_UPDATE,
                payload=payload,
            ),
        )

        # Music started playing
        if state == MediaPlayerState.PLAYING:
            if not self._is_music_content(new_state_obj):
                return

            self._handle_music_started()

        # Music stopped/paused
        elif state in (
            MediaPlayerState.IDLE,
            MediaPlayerState.PAUSED,
            MediaPlayerState.OFF,
        ):
            self._handle_music_stopped()

    def _handle_music_started(self) -> None:
        """Handle music playback started - transition to music mode."""
        # Only auto-enter if feature is enabled
        if not self.music_mode_auto:
            return

        current_mode = self._get_current_mode()

        # Don't override these modes
        if current_mode in (VAMode.HOLD, VAMode.GAME):
            return

        _LOGGER.info(
            "Music playback started on %s, switching to music mode",
            self._config.runtime_data.core.name,
        )

        # Cancel any pending timeout task
        self._cancel_music_timeout_task()

        # Update mode to music
        self._set_mode(VAMode.MUSIC)

    def _handle_music_stopped(self) -> None:
        """Handle music playback stopped - schedule transition to default mode."""
        current_mode = self._get_current_mode()

        if current_mode != VAMode.MUSIC:
            return

        if self.music_mode_timeout <= 0:
            return

        default_mode = self._get_default_mode()
        _LOGGER.info(
            "Music playback stopped on %s, scheduling return to default mode '%s' in %d seconds",
            self._config.runtime_data.core.name,
            default_mode,
            self.music_mode_timeout,
        )

        # Cancel any existing timeout task
        self._cancel_music_timeout_task()

        # Schedule new timeout task
        self.music_timeout_task = self._config.async_create_background_task(
            self._hass,
            self._music_mode_timeout_handler(),
            name=f"Music Mode Timeout - {self._config.runtime_data.core.name}",
        )

    async def _music_mode_timeout_handler(self) -> None:
        """Handle music mode timeout - transition back to default mode."""
        try:
            # Wait for timeout duration
            await asyncio.sleep(min(self.music_mode_timeout, 3600))

            # Verify mode is still music before transitioning
            current_mode = self._get_current_mode()
            if current_mode != VAMode.MUSIC:
                return

            default_mode = self._get_default_mode()
            _LOGGER.info(
                "Music mode timeout expired for %s, returning to default mode '%s'",
                self._config.runtime_data.core.name,
                default_mode,
            )

            # Update mode to default
            self._set_mode(default_mode)

        except asyncio.CancelledError:
            raise

    def _cancel_music_timeout_task(self) -> None:
        """Cancel any existing music mode timeout task."""
        if self.music_timeout_task and not self.music_timeout_task.done():
            self.music_timeout_task.cancel()
            self.music_timeout_task = None

    def _get_current_mode(self) -> str:
        """Get the current mode from the sensor entity."""
        sensor_entity = get_sensor_entity_from_instance(
            self._hass, self._config.entry_id
        )
        if sensor_entity and (state := self._hass.states.get(sensor_entity)):
            return state.attributes.get("mode", VAMode.NORMAL)
        return VAMode.NORMAL

    def _get_default_mode(self) -> str:
        """Get the configured default mode from config options."""
        return self._config.options.get("mode", VAMode.NORMAL)

    def _set_mode(self, mode: str) -> None:
        """Set the mode using the view_assist.set_state service."""
        sensor_entity = get_sensor_entity_from_instance(
            self._hass, self._config.entry_id
        )
        if sensor_entity:
            self._hass.async_create_task(
                self._hass.services.async_call(
                    DOMAIN,
                    "set_state",
                    {
                        "entity_id": sensor_entity,
                        "mode": mode,
                    },
                )
            )

    def _update_sensor_entity(self, updates: dict) -> None:
        """Update sensor entity attributes."""
        self._config.runtime_data.extra_data.update(updates)
        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self._config.entry_id}_event",
            VAEvent(VAEventType.CONFIG_UPDATE),
        )
