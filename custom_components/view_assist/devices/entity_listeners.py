"""Handles entity listeners."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

from awesomeversion import AwesomeVersion

# pylint: disable-next=hass-component-root-import
from homeassistant.components.assist_satellite.entity import AssistSatelliteState
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import STATE_ON
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

from ..assets import AssetClass, AssetsManager  # noqa: TID252
from ..const import (  # noqa: TID252
    CC_CONVERSATION_ENDED_EVENT,
    CYCLE_VIEWS,
    CONF_MUSIC_MODE_AUTO,
    CONF_MUSIC_MODE_TIMEOUT,
    DEVICES,
    DOMAIN,
    ESPHOME_DOMAIN,
    HASSMIC_DOMAIN,
    MIN_DASHBOARD_FOR_OVERLAYS,
    VACA_DOMAIN,
    VAMode,
)
from ..helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_entity_id_from_conversation_device_id,
    get_hassmic_pipeline_status_entity_id,
    get_key,
    get_mute_switch_entity_id,
    get_sensor_entity_from_instance,
)
from ..typed import VAConfigEntry, VAEvent, VAEventType  # noqa: TID252
from .menu import MenuManager

# pylint: disable-next=hass-component-root-import
from .navigation import NavigationManager

_LOGGER = logging.getLogger(__name__)


class EntityListeners:
    """Class to manage entity monitors."""

    @classmethod
    def get(cls, hass: HomeAssistant, config: VAConfigEntry) -> EntityListeners | None:
        """Get the instance for a config entry."""
        try:
            return hass.data[DOMAIN][DEVICES][config.entry_id][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the entity listeners."""
        self.hass = hass
        self.config = config
        self.name = config.runtime_data.core.name

    async def async_setup(self) -> bool:
        """Load module."""
        # Add assist/mic state listener
        AssistEntityListenerHandler(self.hass, self.config).register_listeners()

        # Sensor entity attribute changes
        SensorAttributeChangedHandler(self.hass, self.config).register_listeners()

        # Entity state change listeners
        EntityStateChangedHandler(self.hass, self.config).register_listeners()

        return True

    async def async_unload(self) -> bool:
        """Stop the EntityListeners."""
        return True


class AssistEntityListenerHandler:
    """Class to manage entity listeners for assist/mic status entities."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.mic_integration = None
        self.music_player_entity = config.runtime_data.core.musicplayer_device
        self.music_player_volume: float = 0.0
        self.is_ducked: bool = False
        self.ducking_task: asyncio.Task | None = None

        if mic_device := get_config_entry_by_entity_id(
            hass, config.runtime_data.core.mic_device
        ):
            self.mic_integration = mic_device.domain

    def register_listeners(self) -> None:
        """Register the state change listener for assist/mic status entities."""
        if not self.mic_integration:
            return

        if self.mic_integration == HASSMIC_DOMAIN:
            assist_entity_id = get_hassmic_pipeline_status_entity_id(
                self.hass, self.config.runtime_data.core.mic_device or ""
            )
        else:
            assist_entity_id = self.config.runtime_data.core.mic_device

        if assist_entity_id:
            _LOGGER.debug("Listening for mic device %s", assist_entity_id)
            self.config.async_on_unload(
                async_track_state_change_event(
                    self.hass, assist_entity_id, self.on_state_change
                )
            )
        else:
            _LOGGER.warning(
                "Unable to find entity for pipeline status for %s",
                self.config.runtime_data.core.mic_device,
            )

    async def on_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle state change event for assist/mic status entities."""
        if not event.data.get("new_state"):
            return
        new_state: State | None = event.data["new_state"]
        old_state: State | None = event.data.get("old_state")

        entity_id = new_state.entity_id if new_state else "unknown"

        # If not change to mic state, exit function
        if not old_state or not new_state or old_state.state == new_state.state:
            return

        _LOGGER.debug(
            "Mic state change: %s: %s->%s",
            entity_id,
            old_state.state,
            new_state.state,
        )

        # Display listening/processing overlays, if supported
        await self.do_overlay_event(new_state.state)

        # Volume ducking
        if self.ducking_task and not self.ducking_task.done():
            self.ducking_task.cancel()
        self.ducking_task = self.config.async_create_background_task(
            self.hass,
            self.do_volume_ducking(old_state.state, new_state.state),
            name="VA Volume Ducking Task",
        )

    async def do_volume_ducking(self, old_state: str, new_state: str) -> None:
        """Handle volume ducking for music player when mic is listening."""
        # Volume ducking
        try:
            music_player_integration = get_config_entry_by_entity_id(
                self.hass, self.music_player_entity or ""
            ).domain
        except AttributeError:
            return

        _LOGGER.debug(
            "Performing volume ducking.  Mic is %s, music player is %s",
            self.mic_integration,
            music_player_integration,
        )

        # If device supports onboard ducking, skip
        if self.mic_integration in (
            ESPHOME_DOMAIN,
            VACA_DOMAIN,
        ) and music_player_integration in (
            ESPHOME_DOMAIN,
            VACA_DOMAIN,
        ):
            # HA VPE and VACA have built in volume ducking support
            _LOGGER.debug(
                "Skipping volume ducking as both mic and music player have built-in support"
            )
            return

        # Only proceed if music player is playing
        if self.music_player_entity:
            entity_state = self.hass.states.get(self.music_player_entity)
            if entity_state and entity_state.state != MediaPlayerState.PLAYING:
                _LOGGER.debug(
                    "Music player %s is not playing, skipping volume ducking",
                    self.music_player_entity,
                )
                return

        if (
            self.mic_integration == HASSMIC_DOMAIN and old_state == "wake_word-start"
        ) or (
            self.mic_integration != HASSMIC_DOMAIN
            and new_state == AssistSatelliteState.LISTENING
        ):
            _LOGGER.debug("Mic is listening, ducking music player volume")

            # Ducking volume is a % of current volume of mediaplayer
            ducking_percent = self.config.runtime_data.default.ducking_volume

            if (
                self.music_player_entity
                and (mp_state := self.hass.states.get(self.music_player_entity))
                and (music_player_volume := mp_state.attributes.get("volume_level"))
                is not None
            ):
                _LOGGER.debug("Current music player volume: %s", music_player_volume)

                # Set current volume for restoring later
                if not self.is_ducked:
                    self.music_player_volume = float(music_player_volume or 0)

                # Calculate media player volume for ducking
                ducking_volume = self.music_player_volume * (
                    (100 - (ducking_percent or 0)) / 100
                )

                if self.music_player_volume > ducking_volume:
                    _LOGGER.debug("Ducking music player volume to: %s", ducking_volume)
                    await self.hass.services.async_call(
                        "media_player",
                        "volume_set",
                        {
                            "entity_id": self.music_player_entity,
                            "volume_level": ducking_volume,
                        },
                    )
                    self.is_ducked = True

            else:
                _LOGGER.debug(
                    "Music player volume not found, volume ducking not supported"
                )
                return

        elif (
            self.is_ducked
            and self.music_player_volume > 0
            and (
                (
                    self.mic_integration == HASSMIC_DOMAIN
                    and new_state == "wake_word-start"
                )
                or (
                    self.mic_integration != HASSMIC_DOMAIN
                    and new_state == AssistSatelliteState.IDLE
                )
            )
        ):
            if self.music_player_entity and self.hass.states.get(
                self.music_player_entity
            ):
                await asyncio.sleep(1)
                _LOGGER.debug(
                    "Restoring music player volume: %s", self.music_player_volume
                )

                # Restore gradually to avoid sudden volume change
                if music_player_state := self.hass.states.get(self.music_player_entity):
                    current_music_player_volume = music_player_state.attributes.get(
                        "volume_level"
                    )
                    for i in range(1, 11):
                        volume = min(
                            self.music_player_volume,
                            (current_music_player_volume or 0) + (i * 0.1),
                        )
                        await self.hass.services.async_call(
                            "media_player",
                            "volume_set",
                            {
                                "entity_id": self.music_player_entity,
                                "volume_level": volume,
                            },
                            blocking=True,
                        )
                        if volume == self.music_player_volume:
                            self.is_ducked = False
                            break
                        await asyncio.sleep(0.25)

    async def do_overlay_event(self, state: str) -> None:
        """Trigger overlay update."""
        # Send event to display new javascript overlays
        # Convert state to standard for stt and hassmic
        am = AssetsManager.get(self.hass)
        installed_dashboard = await am.get_installed_version(
            AssetClass.DASHBOARD, "dashboard"
        )
        if (
            installed_dashboard
            and AwesomeVersion(installed_dashboard) >= MIN_DASHBOARD_FOR_OVERLAYS
        ):
            if state in ["vad", "sst-listening"]:
                state = AssistSatelliteState.LISTENING
            elif state in ["start", "intent-processing"]:
                state = AssistSatelliteState.PROCESSING

            assist_prompt = (
                self.config.runtime_data.dashboard.display_settings.assist_prompt
                if not self.config.runtime_data.runtime_config_overrides.assist_prompt
                else self.config.runtime_data.runtime_config_overrides.assist_prompt
            )

            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{self.config.entry_id}_event",
                VAEvent(
                    VAEventType.ASSIST_LISTENING,
                    {
                        "state": state,
                        "style": assist_prompt,
                    },
                ),
            )


class SensorAttributeChangedHandler:
    """Class to manage sensor attribute change listeners."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.sensor_entity: str | None = None

    @property
    def menu_manager(self) -> MenuManager | None:
        """Get the menu manager."""
        return MenuManager.get(self.hass, self.config)

    @property
    def navigation_manager(self) -> NavigationManager | None:
        """Get the navigation manager."""
        return NavigationManager.get(self.hass, self.config)

    def register_listeners(self) -> None:
        """Register the attribute change listener for sensor entities."""
        self.sensor_entity = get_sensor_entity_from_instance(
            self.hass, self.config.entry_id
        )
        if self.sensor_entity:
            _LOGGER.debug("Setting initial states actions for %s", self.sensor_entity)
            if state := self.hass.states.get(self.sensor_entity):
                for attribute, value in state.attributes.items():
                    if hasattr(self, f"on_{attribute}_state_change"):
                        # Call the state change handler for each attribute
                        getattr(self, f"on_{attribute}_state_change")(value)

            _LOGGER.debug("Listening for attribute changes on %s", self.sensor_entity)
            self.config.async_on_unload(
                async_track_state_change_event(
                    self.hass, self.sensor_entity, self._on_attribute_change
                )
            )
        else:
            _LOGGER.warning(
                "Unable to find sensor entity for config entry %s",
                self.config.entry_id,
            )

    @callback
    def _on_attribute_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle attribute change events for sensor entities."""
        old_state: State = event.data.get("old_state")
        new_state: State = event.data.get("new_state")

        old_attrs = old_state.attributes if old_state else {}
        new_attrs = new_state.attributes if new_state else {}

        for attribute, value in new_attrs.items():
            if old_attrs.get(attribute) != new_attrs.get(attribute):
                if hasattr(self, f"on_{attribute}_state_change"):
                    _LOGGER.debug(
                        "Attribute change detected on sensor entity: %s -> %s to %s",
                        event.data["entity_id"],
                        attribute,
                        value,
                    )
                    getattr(self, f"on_{attribute}_state_change")(value)

    def on_do_not_disturb_state_change(self, new_state: str) -> None:
        """Handle DND state change events for sensor entities."""
        _LOGGER.debug("DND state change detected on sensor entity: %s", new_state)

        if self.menu_manager:
            if new_state == "on":
                self.menu_manager.add_items("dnd")
            else:
                self.menu_manager.remove_items("dnd")

    def on_mode_state_change(self, new_mode: str) -> None:
        """Handle mode state change events for sensor entities."""
        _LOGGER.debug("Mode state change detected on sensor entity: %s", new_mode)

        if self.menu_manager:
            mode_icons = [VAMode.HOLD, VAMode.CYCLE]
            # Remove all mode icons first
            self.menu_manager.remove_items(mode_icons)
            # Add current mode icon if it should be shown
            if new_mode in mode_icons:
                self.menu_manager.add_items(new_mode)

        if new_mode != VAMode.CYCLE:
            if self.navigation_manager:
                self.navigation_manager.stop_cycle_display()

        if new_mode == VAMode.NORMAL:
            # Add navigate to default view
            if self.navigation_manager:
                self.navigation_manager.navigate_home()
        elif new_mode == VAMode.MUSIC:
            # Add navigate to music view
            if self.navigation_manager:
                self.navigation_manager.browser_navigate(
                    self.config.runtime_data.dashboard.music
                )
        elif new_mode == VAMode.CYCLE:
            # Start cycling views
            if self.navigation_manager:
                self.navigation_manager.start_display_view_cycle(CYCLE_VIEWS)

        elif new_mode == VAMode.HOLD:
            # Hold mode, so cancel any revert timer
            if self.navigation_manager:
                self.navigation_manager.cancel_display_revert_task()
                self.config.runtime_data.extra_data["hold_path"] = (
                    self.config.runtime_data.extra_data.get("current_path")
                )

        # If hold mode disabled, go home and clear hold path
        if new_mode != VAMode.HOLD and self.config.runtime_data.extra_data.get(
            "hold_path"
        ):
            if self.navigation_manager:
                self.navigation_manager.navigate_home()
            self.config.runtime_data.extra_data["hold_path"] = None


class EntityStateChangedHandler:
    """Class to manage entity state change listeners."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.entity_id: str | None = None

        # Music mode auto-switching configuration
        self.music_mode_auto = config.options.get(CONF_MUSIC_MODE_AUTO, False)
        self.music_mode_timeout = config.options.get(CONF_MUSIC_MODE_TIMEOUT, 300)
        self.music_timeout_task: asyncio.Task | None = None

    def register_listeners(self) -> None:
        """Register the state change listener for entities."""
        # Add microphone mute switch listener
        if mute_switch := get_mute_switch_entity_id(
            self.hass, self.config.runtime_data.core.mic_device
        ):
            self._add_entity_state_listener(mute_switch, self._async_on_mic_mute_change)

        # Add media player mute listener
        if mediaplayer_device := self.config.runtime_data.core.mediaplayer_device:
            self._add_entity_state_listener(
                mediaplayer_device, self._async_on_mediaplayer_device_mute_change
            )

        # Add intent sensor listener
        if intent_device := self.config.runtime_data.core.intent_device:
            self._add_entity_state_listener(
                intent_device, self._async_on_intent_device_change
            )

        # Add listener for custom conversation intent event
        self.config.async_on_unload(
            self.hass.bus.async_listen(
                CC_CONVERSATION_ENDED_EVENT,
                self._async_cc_on_conversation_ended_handler,
            )
        )

        # Add music player state listener for auto mode switching
        if self._should_monitor_music_player():
            musicplayer_device = self.config.runtime_data.core.musicplayer_device

            # Check initial state, enter music mode if already playing
            if musicplayer_state := self.hass.states.get(musicplayer_device):
                if musicplayer_state.state == MediaPlayerState.PLAYING:
                    if self._is_music_content(musicplayer_state):
                        self._handle_music_started()

            # Add listener for future state changes
            self.config.async_on_unload(
                async_track_state_change_event(
                    self.hass,
                    musicplayer_device,
                    self._async_on_musicplayer_device_state_change,
                )
            )

    def _add_entity_state_listener(
        self, entity_id: str, listener: Callable[[Event[EventStateChangedData]], None]
    ) -> None:
        """Add a state listener for an entity."""

        # Call listener handler with current state
        if state := self.hass.states.get(entity_id):
            _LOGGER.debug("Setting initial state for %s", entity_id)
            listener(
                Event[EventStateChangedData](
                    event_type="initial_state",
                    data=EventStateChangedData(
                        entity_id=entity_id,
                        new_state=state,
                    ),
                    context=Context(id="initial_state"),
                )
            )

        # Add listener
        self.config.async_on_unload(
            async_track_state_change_event(self.hass, entity_id, listener)
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
    def _async_on_mic_mute_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle microphone mute state changes via menu manager."""
        if not self._validate_event(event):
            return

        mic_mute_new_state = event.data.get("new_state").state
        _LOGGER.debug("Mic mute state changed to %s", mic_mute_new_state)

        # Use menu manager to update status icons
        if menu_manager := MenuManager.get(self.hass, self.config):
            if mic_mute_new_state == STATE_ON:
                menu_manager.add_items(items=["mic"], menu=False)
            else:
                menu_manager.remove_items(items=["mic"], menu=False)

    @callback
    def _async_on_mediaplayer_device_mute_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle media player mute state changes via menu manager."""
        if not self._validate_event(event):
            return

        mp_mute_new_state = event.data.get("new_state").attributes.get(
            "is_volume_muted", False
        )

        # If not change to mute state, exit function
        if (
            event.data.get("old_state")
            and event.data["old_state"].attributes.get("is_volume_muted")
            == mp_mute_new_state
        ):
            return

        _LOGGER.debug("Media player mute state changed to %s", mp_mute_new_state)

        # Use menu manager to update status icons
        if menu_manager := MenuManager.get(self.hass, self.config):
            if mp_mute_new_state == STATE_ON:
                menu_manager.add_items(items=["mediaplayer"], menu=False)
            else:
                menu_manager.remove_items(items=["mediaplayer"], menu=False)

    @callback
    def _async_on_intent_device_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle intent device state changes."""
        if not self._validate_event(event):
            return

        new_state: State = event.data["new_state"]
        if intent_output := new_state.attributes.get("intent_output"):
            speech_text = get_key("response.speech.plain.speech", intent_output)
            _LOGGER.debug("Intent output received: %s", speech_text)
            # Set updates to apply to sensor entity
            updates = {}
            processed_locally = new_state.attributes.get("processed_locally", False)
            navigation_manager = NavigationManager.get(self.hass, self.config)

            # Add speech text to sensor entity
            if speech_text:
                updates["last_said"] = speech_text

            # Get changed entities and format for buttons
            if changed_entities := get_key("response.data.success", intent_output):
                prefixes = (
                    "light",
                    "switch",
                    "cover",
                    "boolean",
                    "input_boolean",
                    "fan",
                )

                # Establish changes
                entities = [
                    item["id"]
                    for item in changed_entities
                    if str(item.get("id", "")).startswith(prefixes)
                ]
                todos = [
                    item["id"]
                    for item in changed_entities
                    if str(item.get("id", "")).startswith("todo")
                ]

                # Process changes to update sensor and navigate view if needed
                if entities:
                    _LOGGER.debug("Entities affected: %s", entities)
                    entities_output = [
                        {
                            "type": "custom:button-card",
                            "entity": entity,
                            "tap_action": {"action": "toggle"},
                            "double_tap_action": {"action": "more-info"},
                        }
                        for entity in entities
                    ]
                    updates["intent_entities"] = entities_output
                    self._update_sensor_entity(updates)
                    if navigation_manager:
                        navigation_manager.browser_navigate(
                            self.config.runtime_data.dashboard.intent
                        )
                elif todos:
                    _LOGGER.debug("Todo lists affected: %s", todos)
                    updates["list"] = todos[0]  # Just use the first todo list
                    self._update_sensor_entity(updates)
                    if navigation_manager:
                        navigation_manager.browser_navigate(
                            self.config.runtime_data.dashboard.list_view
                        )
            # Checks if AI response or if no speech is returned
            elif not processed_locally and speech_text != "*":
                _LOGGER.debug("No entities or todo lists affected")
                word_count = len(speech_text.split())
                message_font_size = ["10vw", "8vw", "6vw", "4vw"][
                    min(word_count // 6, 3)
                ]
                # Navigate first to trigger title clear
                if navigation_manager:
                    navigation_manager.browser_navigate("view-assist/info")
                # Then set the title/message after navigation to prevent clearing
                updates.update(
                    {
                        "title": "AI Response",
                        "message_font_size": message_font_size,
                        "message": speech_text,
                    }
                )
                self._update_sensor_entity(updates)

    @callback
    def _async_cc_on_conversation_ended_handler(self, event: Event):
        """Handle conversation ended event from custom conversation or vaca."""
        # Get VA entity from device id
        entity_id = get_sensor_entity_from_instance(self.hass, self.config.entry_id)
        if (
            event.data.get("device_id")
            and get_entity_id_from_conversation_device_id(
                self.hass, event.data["device_id"]
            )
            == entity_id
        ):
            _LOGGER.debug("Received CC event for %s: %s", entity_id, event)
            # mic device id matches this VA entity
            # reformat event data
            state = get_key("result.response.speech.plain.speech", event.data)
            attributes = {"intent_output": event.data["result"]}

            # Wrap event into HA State update event
            state = State(entity_id=entity_id, state=state, attributes=attributes)
            self._async_on_intent_device_change(
                Event[EventStateChangedData](
                    event_type=CC_CONVERSATION_ENDED_EVENT,
                    data=EventStateChangedData(new_state=state),
                )
            )
        else:
            _LOGGER.debug(
                "Received CC event for %s but device id does not match: %s",
                entity_id,
                event.data["device_id"],
            )

    def _should_monitor_music_player(self) -> bool:
        """Check if music player monitoring should be enabled."""
        musicplayer = self.config.runtime_data.core.musicplayer_device

        if not musicplayer:
            return False

        # Only monitor if at least one feature is enabled
        if not self.music_mode_auto and self.music_mode_timeout <= 0:
            return False

        return True

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
    def _async_on_musicplayer_device_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle music player state changes for auto mode switching."""
        if not self._validate_event(event):
            return

        new_state_obj = event.data.get("new_state")
        old_state_obj = event.data.get("old_state")

        new_state = new_state_obj.state
        old_state = old_state_obj.state if old_state_obj else None

        if old_state == new_state:
            return

        # Music started playing
        if new_state == MediaPlayerState.PLAYING:
            if not self._is_music_content(new_state_obj):
                return

            self._handle_music_started()
        # Music stopped/paused
        elif new_state in (
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
            self.config.runtime_data.core.name,
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
            self.config.runtime_data.core.name,
            default_mode,
            self.music_mode_timeout,
        )

        # Cancel any existing timeout task
        self._cancel_music_timeout_task()

        # Schedule new timeout task
        self.music_timeout_task = self.config.async_create_background_task(
            self.hass,
            self._music_mode_timeout_handler(),
            name=f"Music Mode Timeout - {self.config.runtime_data.core.name}",
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
                self.config.runtime_data.core.name,
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
        sensor_entity = get_sensor_entity_from_instance(self.hass, self.config.entry_id)
        if sensor_entity and (state := self.hass.states.get(sensor_entity)):
            return state.attributes.get("mode", VAMode.NORMAL)
        return VAMode.NORMAL

    def _get_default_mode(self) -> str:
        """Get the configured default mode from config options."""
        return self.config.options.get("mode", VAMode.NORMAL)

    def _set_mode(self, mode: str) -> None:
        """Set the mode using the view_assist.set_state service."""
        sensor_entity = get_sensor_entity_from_instance(self.hass, self.config.entry_id)
        if sensor_entity:
            self.hass.async_create_task(
                self.hass.services.async_call(
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
        self.config.runtime_data.extra_data.update(updates)
        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{self.config.entry_id}_event",
            VAEvent(VAEventType.CONFIG_UPDATE),
        )
