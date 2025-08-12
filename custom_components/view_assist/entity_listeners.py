"""Handles entity listeners."""

import asyncio
from asyncio import Task
from datetime import datetime as dt
import logging
import random

from awesomeversion import AwesomeVersion

from homeassistant.components.assist_satellite.entity import AssistSatelliteState
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_MODE
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.start import async_at_started

from .assets import ASSETS_MANAGER, AssetClass, AssetsManager
from .const import (
    BROWSERMOD_DOMAIN,
    CC_CONVERSATION_ENDED_EVENT,
    CONF_DO_NOT_DISTURB,
    CYCLE_VIEWS,
    DEFAULT_VIEW_INFO,
    DOMAIN,
    HASSMIC_DOMAIN,
    MIN_DASHBOARD_FOR_OVERLAYS,
    REMOTE_ASSIST_DISPLAY_DOMAIN,
    USE_VA_NAVIGATION_FOR_BROWSERMOD,
    VA_ATTRIBUTE_UPDATE_EVENT,
    VA_BACKGROUND_UPDATE_EVENT,
    VACA_DOMAIN,
    VAMode,
)
from .helpers import (
    async_get_download_image,
    async_get_filesystem_images,
    get_config_entry_by_entity_id,
    get_device_name_from_id,
    get_display_type_from_browser_id,
    get_entity_attribute,
    get_entity_id_from_conversation_device_id,
    get_hassmic_pipeline_status_entity_id,
    get_key,
    get_mute_switch_entity_id,
    get_revert_settings_for_mode,
    get_sensor_entity_from_instance,
)
from .typed import VABackgroundMode, VAConfigEntry, VADisplayType, VAEvent

_LOGGER = logging.getLogger(__name__)


class EntityListeners:
    """Class to manage entity monitors."""

    def __init__(self, hass: HomeAssistant, config_entry: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config_entry = config_entry
        self.browser_or_device_id: str | None = None

        self.revert_view_task: Task | None = None
        self.cycle_view_task: Task | None = None
        self.rotate_background_task: Task | None = None

        self.music_player_volume: float | None = None

        # Add browser navigate service listener
        config_entry.async_on_unload(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{config_entry.entry_id}_browser_navigate",
                self._handle_browser_navigate_service_call,
            )
        )

        # Add listener to set_state service to call sensor_attribute_changed
        config_entry.async_on_unload(
            hass.bus.async_listen(
                VA_ATTRIBUTE_UPDATE_EVENT.format(config_entry.entry_id),
                self.async_set_state_changed_attribute,
            )
        )

        # Add mic device/wake word entity listening listner for volume ducking
        if config_entry.runtime_data.default.ducking_volume is not None:
            try:
                mic_integration = get_config_entry_by_entity_id(
                    self.hass, self.config_entry.runtime_data.core.mic_device
                ).domain
                if mic_integration == HASSMIC_DOMAIN:
                    entity_id = get_hassmic_pipeline_status_entity_id(
                        hass, self.config_entry.runtime_data.core.mic_device
                    )
                else:
                    entity_id = self.config_entry.runtime_data.core.mic_device

                if entity_id:
                    _LOGGER.debug("Listening for mic device %s", entity_id)
                    config_entry.async_on_unload(
                        async_track_state_change_event(
                            hass,
                            entity_id,
                            self._async_on_mic_state_change,
                        )
                    )
                else:
                    _LOGGER.warning(
                        "Unable to find entity for pipeline status for %s",
                        self.config_entry.runtime_data.core.mic_device,
                    )
            except AttributeError:
                _LOGGER.error(
                    "Error getting mic entity for %s",
                    self.config_entry.runtime_data.core.mic_device,
                )

        # Add microphone mute switch listener
        mute_switch = get_mute_switch_entity_id(
            hass, config_entry.runtime_data.core.mic_device
        )
        if mute_switch:
            config_entry.async_on_unload(
                async_track_state_change_event(
                    hass, mute_switch, self._async_on_mic_change
                )
            )

        # Add media player mute listener
        mediaplayer_device = self.config_entry.data["mediaplayer_device"]
        if mediaplayer_device:
            config_entry.async_on_unload(
                async_track_state_change_event(
                    hass,
                    mediaplayer_device,
                    self._async_on_mediaplayer_device_mute_change,
                )
            )

        # Add intent sensor listener
        intent_device = self.config_entry.data.get("intent_device")
        if intent_device:
            config_entry.async_on_unload(
                async_track_state_change_event(
                    hass, intent_device, self._async_on_intent_device_change
                )
            )

        # Add listener for custom conversation intent event
        config_entry.async_on_unload(
            hass.bus.async_listen(
                CC_CONVERSATION_ENDED_EVENT,
                self._async_cc_on_conversation_ended_handler,
            )
        )

        async_at_started(hass, self._after_ha_start)

        self.update_entity()

    async def _after_ha_start(self, *args):
        """Run after HA has started."""
        # Wait for instance to finish loading
        while self.config_entry.state != ConfigEntryState.LOADED:
            await asyncio.sleep(0.1)

        # Run display rotate task if set for device
        if (
            self.config_entry.runtime_data.dashboard.background_settings.background_mode
            != VABackgroundMode.DEFAULT_BACKGROUND
        ):
            # Set task based on mode
            if (
                self.config_entry.runtime_data.dashboard.background_settings.background_mode
                == VABackgroundMode.LINKED
            ):
                if self.config_entry.runtime_data.dashboard.background_settings.rotate_background_linked_entity:
                    _LOGGER.debug(
                        "Starting rotate background linked image listener for %s, linked to %s",
                        self.config_entry.runtime_data.core.name,
                        self.config_entry.runtime_data.dashboard.background_settings.rotate_background_linked_entity,
                    )
                    # Add listener for background changes
                    self.config_entry.async_on_unload(
                        self.hass.bus.async_listen(
                            VA_BACKGROUND_UPDATE_EVENT.format(
                                self.config_entry.runtime_data.dashboard.background_settings.rotate_background_linked_entity
                            ),
                            self.async_set_background_image,
                        )
                    )
                    # Set initial background from linked entity
                    await self.async_set_background_image(
                        get_entity_attribute(
                            self.hass,
                            self.config_entry.runtime_data.dashboard.background_settings.rotate_background_linked_entity,
                            "background",
                        )
                    )
                else:
                    _LOGGER.warning(
                        "%s is set to link its background image but no linked entity provided",
                        self.config_entry.runtime_data.core.name,
                    )
            else:
                _LOGGER.debug(
                    "Starting rotate background image task for %s",
                    self.config_entry.runtime_data.core.name,
                )
                self.rotate_background_task = (
                    self.config_entry.async_create_background_task(
                        self.hass,
                        self.async_background_image_rotation_task(),
                        f"{self.config_entry.runtime_data.core.name} rotate image task",
                    )
                )

    async def _display_revert_delay(self, path: str, timeout: int = 0):
        """Display revert function.  To be called from task."""
        if timeout:
            await asyncio.sleep(timeout)
            await self.async_browser_navigate(path, is_revert_action=True)

    def _cancel_display_revert_task(self):
        """Cancel any existing revert timer task."""
        if self.revert_view_task and not self.revert_view_task.done():
            _LOGGER.debug("Cancelled revert task")
            self.revert_view_task.cancel()
            self.revert_view_task = None

    async def _handle_browser_navigate_service_call(self, args):
        """Navigate browser to defined view.

        Optionally revert to another view after timeout.
        """
        path = args["path"]
        await self.async_browser_navigate(path)

    async def async_browser_navigate(
        self,
        path: str,
        is_revert_action: bool = False,
    ):
        """Navigate browser to defined view.

        Optionally revert to another view after timeout.
        """

        # If new navigate before revert timer has expired, cancel revert timer.
        if not is_revert_action:
            self._cancel_display_revert_task()

        # Store current path in entity attributes to help menu filtering
        entity_id = get_sensor_entity_from_instance(
            self.hass, self.config_entry.entry_id
        )

        # Update current path attribute
        service_data = {
            "entity_id": entity_id,
            "current_path": path,
        }

        # Clear stale title to prevent context bleeding between views
        service_data.update({
            "title": "",
        })

        await self.hass.services.async_call(
            DOMAIN,
            "set_state",
            service_data,
        )

        # Do navigation and set revert if needed
        browser_id = get_device_name_from_id(
            self.hass, self.config_entry.runtime_data.core.display_device
        )
        display_type = get_display_type_from_browser_id(self.hass, browser_id)

        _LOGGER.debug(
            "Navigating: %s, browser_id: %s, path: %s, display_type: %s, mode: %s",
            self.config_entry.runtime_data.core.name,
            browser_id,
            path,
            display_type,
            self.config_entry.runtime_data.default.mode,
        )

        # If using BrowserMod
        if display_type == VADisplayType.BROWSERMOD:
            if not self.browser_or_device_id:
                self.browser_or_device_id = browser_id

            if USE_VA_NAVIGATION_FOR_BROWSERMOD:
                # Use own VA navigation
                async_dispatcher_send(
                    self.hass,
                    f"{DOMAIN}_{self.config_entry.entry_id}_event",
                    VAEvent("navigate", {"path": path}),
                )
            else:
                await self.hass.services.async_call(
                    BROWSERMOD_DOMAIN,
                    "navigate",
                    {"browser_id": self.browser_or_device_id, "path": path},
                )

        # If using RAD
        elif display_type == VADisplayType.REMOTE_ASSIST_DISPLAY:
            if not self.browser_or_device_id:
                device_reg = dr.async_get(self.hass)
                if device := device_reg.async_get_device(
                    identifiers={(REMOTE_ASSIST_DISPLAY_DOMAIN, browser_id)}
                ):
                    self.browser_or_device_id = device.id
            await self.hass.services.async_call(
                REMOTE_ASSIST_DISPLAY_DOMAIN,
                "navigate",
                {"target": self.browser_or_device_id, "path": path},
            )

        else:
            # Use own VA navigation
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{self.config_entry.entry_id}_event",
                VAEvent("navigate", {"path": path}),
            )

        # If this was a revert action, end here
        if is_revert_action:
            return

        # Find required revert action
        revert, revert_view = get_revert_settings_for_mode(
            self.config_entry.runtime_data.default.mode
        )
        revert_path = (
            getattr(self.config_entry.runtime_data.dashboard, revert_view)
            if revert_view
            else None
        )

        # Set revert action if required
        if revert and path != revert_path:
            timeout = self.config_entry.runtime_data.default.view_timeout
            _LOGGER.debug("Adding revert to %s in %ss", revert_path, timeout)
            self.revert_view_task = self.hass.async_create_task(
                self._display_revert_delay(revert_path, timeout)
            )

    async def async_cycle_display_view(self, views: list[str]):
        """Cycle display."""

        view_index = 0
        _LOGGER.debug("Cycle display started")
        while self.config_entry.runtime_data.default.mode == VAMode.CYCLE:
            view_index = view_index % len(views)
            _LOGGER.debug("Cycling to view: %s", views[view_index])
            await self.async_browser_navigate(
                f"{self.config_entry.runtime_data.dashboard.dashboard}/{views[view_index]}",
            )
            view_index += 1
            await asyncio.sleep(self.config_entry.runtime_data.default.view_timeout)

    async def async_background_image_rotation_task(self):
        """Task to get background image for image rotation."""
        source = (
            self.config_entry.runtime_data.dashboard.background_settings.background_mode
        )
        path = self.config_entry.runtime_data.dashboard.background_settings.rotate_background_path
        interval = self.config_entry.runtime_data.dashboard.background_settings.rotate_background_interval
        image_index = 0

        # Clean path
        path.removeprefix("/").removesuffix("/")

        try:
            if source in [
                VABackgroundMode.LOCAL_SEQUENCE,
                VABackgroundMode.LOCAL_RANDOM,
            ]:
                image_list = await async_get_filesystem_images(self.hass, path)
                if not image_list:
                    return

            while True:
                if source == "local_sequence":
                    image = image_list[image_index]
                    image_index += 1
                    if image_index == len(image_list):
                        image_index = 0

                elif source == "local_random":
                    image = random.choice(image_list)

                elif source == "download":
                    image = await async_get_download_image(
                        self.hass, self.config_entry, path
                    )
                else:
                    return

                image_url = (
                    image.as_uri()
                    .replace("file://", "")
                    .replace(self.hass.config.config_dir, "")
                )

                # Add parameter to override cache
                image_url = f"{image_url}?v={dt.now().strftime('%Y%m%d%H%M%S')}"

                # Set new background
                await self.async_set_background_image(image_url)

                # Interval is in minutes.  Convert to seconds
                await asyncio.sleep(interval * 60)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error in image rotation.  %s", ex)

    async def async_set_background_image(self, image_url_or_event: str | Event):
        """Set random background image either from url or event."""
        if isinstance(image_url_or_event, Event):
            # This was raised from a linked entity for the background
            image_url = image_url_or_event.data["new_value"]
        else:
            image_url = image_url_or_event

        if image_url:
            entity_id = get_sensor_entity_from_instance(
                self.hass, self.config_entry.entry_id
            )
            _LOGGER.debug(
                "Setting %s background image to %s",
                self.config_entry.runtime_data.core.name,
                image_url,
            )

            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                service_data={
                    "entity_id": entity_id,
                    "background": image_url,
                },
            )

    def update_entity(self):
        """Dispatch message that entity is listening for to update."""
        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{self.config_entry.entry_id}_update",
        )

    # ---------------------------------------------------------------------------------------
    # Actions for monitoring changes to external entities
    # ---------------------------------------------------------------------------------------

    async def _async_on_mic_state_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle mic state change event for volume ducking."""

        # If not change to mic state, exit function
        if (
            not event.data.get("old_state")
            or not event.data.get("new_state")
            or event.data["old_state"].state == event.data["new_state"].state
        ):
            return

        try:
            mic_integration = get_config_entry_by_entity_id(
                self.hass, self.config_entry.runtime_data.core.mic_device
            ).domain
        except AttributeError:
            return

        _LOGGER.debug(
            "Mic state change: %s: %s->%s",
            mic_integration,
            event.data["old_state"].state,
            event.data["new_state"].state,
        )

        # Send event to display new javascript overlays
        # Convert state to standard for stt and hassmic
        am: AssetsManager = self.hass.data[DOMAIN][ASSETS_MANAGER]
        installed_dashboard = await am.get_installed_version(
            AssetClass.DASHBOARD, "dashboard"
        )
        if (
            installed_dashboard
            and AwesomeVersion(installed_dashboard) >= MIN_DASHBOARD_FOR_OVERLAYS
        ):
            state = event.data["new_state"].state
            if state in ["vad", "sst-listening"]:
                state = AssistSatelliteState.LISTENING
            elif state in ["start", "intent-processing"]:
                state = AssistSatelliteState.PROCESSING

            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{self.config_entry.entry_id}_event",
                VAEvent(
                    "listening",
                    {
                        "state": state,
                        "style": self.config_entry.runtime_data.dashboard.display_settings.assist_prompt,
                    },
                ),
            )

        # Volume ducking
        music_player_entity_id = self.config_entry.runtime_data.core.musicplayer_device
        try:
            music_player_integration = get_config_entry_by_entity_id(
                self.hass, music_player_entity_id
            ).domain
        except AttributeError:
            return

        if mic_integration in (
            "esphome",
            VACA_DOMAIN,
        ) and music_player_integration in ("esphome", VACA_DOMAIN):
            # HA VPE already supports volume ducking
            return

        if (
            self.hass.states.get(music_player_entity_id).state
            != MediaPlayerState.PLAYING
        ):
            return

        old_state = event.data["old_state"].state
        new_state = event.data["new_state"].state

        if (mic_integration == HASSMIC_DOMAIN and old_state == "wake_word-start") or (
            mic_integration != HASSMIC_DOMAIN
            and new_state == AssistSatelliteState.LISTENING
        ):
            _LOGGER.debug("Mic is listening, ducking music player volume")

            # Ducking volume is a % of current volume of mediaplayer
            ducking_percent = self.config_entry.runtime_data.default.ducking_volume

            if music_player_volume := self.hass.states.get(
                music_player_entity_id
            ).attributes.get("volume_level"):
                _LOGGER.debug("Current music player volume: %s", music_player_volume)
                # Set current volume for restoring later
                self.music_player_volume = music_player_volume

                # Calculate media player volume for ducking
                ducking_volume = music_player_volume * ((100 - ducking_percent) / 100)

                if self.music_player_volume > ducking_volume:
                    _LOGGER.debug("Ducking music player volume to: %s", ducking_volume)
                    await self.hass.services.async_call(
                        "media_player",
                        "volume_set",
                        {
                            "entity_id": music_player_entity_id,
                            "volume_level": ducking_volume,
                        },
                    )

            else:
                _LOGGER.debug(
                    "Music player volume not found, volume ducking not supported"
                )
                return

        elif (
            (mic_integration == HASSMIC_DOMAIN and new_state == "wake_word-start")
            or (
                mic_integration != HASSMIC_DOMAIN
                and new_state == AssistSatelliteState.IDLE
            )
        ) and self.music_player_volume is not None:
            if self.hass.states.get(music_player_entity_id):
                await asyncio.sleep(1)
                _LOGGER.debug(
                    "Restoring music player volume: %s", self.music_player_volume
                )
                # Restore gradually to avoid sudden volume change
                current_music_player_volume = self.hass.states.get(
                    music_player_entity_id
                ).attributes.get("volume_level")
                for i in range(1, 11):
                    volume = min(
                        self.music_player_volume,
                        current_music_player_volume + (i * 0.1),
                    )
                    await self.hass.services.async_call(
                        "media_player",
                        "volume_set",
                        {
                            "entity_id": music_player_entity_id,
                            "volume_level": volume,
                        },
                        blocking=True,
                    )
                    if volume == self.music_player_volume:
                        break
                    await asyncio.sleep(0.25)
                self.music_player_volume = None

    @callback
    def _async_on_mic_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle microphone mute state changes via menu manager."""
        event_new = event.data.get("new_state")
        if not event_new:
            return

        mic_mute_new_state = event.data["new_state"].state

        # If not change to mic mute state, exit function
        if (
            not event.data.get("old_state")
            or event.data["old_state"].state == mic_mute_new_state
        ):
            return

        _LOGGER.debug("MIC MUTE: %s", mic_mute_new_state)

        # Get entity ID for this config entry
        entity_id = get_sensor_entity_from_instance(
            self.hass, self.config_entry.entry_id
        )

        # Get menu manager to update system icons
        menu_manager = self.hass.data[DOMAIN]["menu_manager"]

        # Use menu manager to update system icons
        if mic_mute_new_state == "on":
            self.hass.async_create_task(
                menu_manager.update_system_icons(entity_id, add_icons=["mic"])
            )
        else:
            self.hass.async_create_task(
                menu_manager.update_system_icons(entity_id, remove_icons=["mic"])
            )

    @callback
    def _async_on_mediaplayer_device_mute_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle media player mute state changes via menu manager."""
        if not event.data.get("new_state"):
            return

        mp_mute_new_state = event.data["new_state"].attributes.get(
            "is_volume_muted", False
        )

        # If not change to mute state, exit function
        if (
            not event.data.get("old_state")
            or event.data["old_state"].attributes.get("is_volume_muted")
            == mp_mute_new_state
        ):
            return

        _LOGGER.debug("MP MUTE: %s", mp_mute_new_state)

        # Get entity ID for this config entry
        entity_id = get_sensor_entity_from_instance(
            self.hass, self.config_entry.entry_id
        )

        # Get menu manager to update system icons
        menu_manager = self.hass.data[DOMAIN]["menu_manager"]

        # Use menu manager to update system icons
        if mp_mute_new_state:
            self.hass.async_create_task(
                menu_manager.update_system_icons(entity_id, add_icons=["mediaplayer"])
            )
        else:
            self.hass.async_create_task(
                menu_manager.update_system_icons(
                    entity_id, remove_icons=["mediaplayer"]
                )
            )

    async def _async_cc_on_conversation_ended_handler(self, event: Event):
        """Handle conversation ended event from custom conversation or vaca."""
        # Get VA entity from device id
        entity_id = get_sensor_entity_from_instance(
            self.hass, self.config_entry.entry_id
        )
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
            await self._async_on_intent_device_change(
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

    async def _async_on_intent_device_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        entity_id = get_sensor_entity_from_instance(
            self.hass, self.config_entry.entry_id
        )
        if intent_new_state := event.data["new_state"].attributes.get("intent_output"):
            speech_text = get_key("response.speech.plain.speech", intent_new_state)
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                service_data={
                    "entity_id": entity_id,
                    "last_said": speech_text,
                },
            )

            # Get changed entities and format for buttons
            changed_entities = get_key("response.data.success", intent_new_state)
            prefixes = ("light", "switch", "cover", "boolean", "input_boolean", "fan")

            # Filtering the list based on prefixes
            filtered_list = (
                [
                    item["id"]
                    for item in changed_entities
                    if item.get("id", "").startswith(prefixes)
                ]
                if changed_entities
                else []
            )
            # Creating the final result
            filtered_entities = [
                {
                    "type": "custom:button-card",
                    "entity": entity,
                    "tap_action": {"action": "toggle"},
                    "double_tap_action": {"action": "more-info"},
                }
                for entity in filtered_list
            ]

            todo_entities = (
                [
                    item["id"]
                    for item in changed_entities
                    if item.get("id", "").startswith("todo")
                ]
                if changed_entities
                else []
            )

            # Check to make sure filtered_entities is not empty before proceeding
            if filtered_entities:
                await self.hass.services.async_call(
                    DOMAIN,
                    "set_state",
                    service_data={
                        "entity_id": entity_id,
                        "intent_entities": filtered_entities,
                    },
                )
                await self.async_browser_navigate(
                    self.config_entry.runtime_data.dashboard.intent
                )
            # If there are no filtered entities but there is a todo entity, show the list view
            elif todo_entities:
                await self.hass.services.async_call(
                    DOMAIN,
                    "set_state",
                    service_data={
                        "entity_id": entity_id,
                        # If there are somehow multiple affected lists, just use the first one
                        "list": todo_entities[0],
                    },
                )
                await self.async_browser_navigate(
                    self.config_entry.runtime_data.dashboard.list_view
                )

            elif not event.data["new_state"].attributes.get("processed_locally", False):
                word_count = len(speech_text.split())
                message_font_size = ["10vw", "8vw", "6vw", "4vw"][
                    min(word_count // 6, 3)
                ]
                await self.async_browser_navigate(
                    f"{self.config_entry.runtime_data.dashboard.dashboard}/{DEFAULT_VIEW_INFO}"
                )
                await self.hass.services.async_call(
                    DOMAIN,
                    "set_state",
                    service_data={
                        "entity_id": entity_id,
                        "title": "AI Response",
                        "message_font_size": message_font_size,
                        "message": speech_text,
                    },
                )

    # ---------------------------------------------------------------------------------------
    # Actions for attributes changed via the set_state service
    # ---------------------------------------------------------------------------------------

    async def async_set_state_changed_attribute(self, event: Event):
        """Call when a sensor attribute is changed by the set_state service."""

        # This function is only called if the new_value != old_value so no need to test
        # for that.  If you are here, an attribute has changed.
        attribute = event.data.get("attribute")
        old_value = event.data.get("old_value")
        new_value = event.data.get("new_value")

        _LOGGER.debug(
            "Attribute changed: %s - old value: %s, new value: %s",
            attribute,
            old_value,
            new_value,
        )

        if attribute == CONF_DO_NOT_DISTURB:
            await self._async_on_dnd_device_state_change(event)

        if attribute == CONF_MODE:
            await self._async_on_mode_state_change(event)

    async def _async_on_dnd_device_state_change(self, event: Event) -> None:
        """Handle DND state changes via menu manager."""
        # This is called from our set_service event listener and therefore event data is
        # slightly different.  See set_state_changed_attribute above
        dnd_new_state = event.data["new_value"]

        _LOGGER.debug("DND STATE: %s", dnd_new_state)

        # Get entity ID for this config entry
        entity_id = get_sensor_entity_from_instance(
            self.hass, self.config_entry.entry_id
        )

        # Get menu manager to update system icons
        menu_manager = self.hass.data[DOMAIN]["menu_manager"]

        # Use menu manager to update system icons
        if dnd_new_state:
            await menu_manager.update_system_icons(entity_id, add_icons=["dnd"])
        else:
            await menu_manager.update_system_icons(entity_id, remove_icons=["dnd"])

    async def _async_on_mode_state_change(self, event: Event) -> None:
        """Handle mode state changes via menu manager."""
        new_mode = event.data["new_value"]
        r = self.config_entry.runtime_data

        _LOGGER.debug("MODE STATE: %s", new_mode)

        # Get entity ID for this config entry
        entity_id = get_sensor_entity_from_instance(
            self.hass, self.config_entry.entry_id
        )

        # Get menu manager to update system icons
        menu_manager = self.hass.data[DOMAIN]["menu_manager"]

        # Define mode icons that should be shown
        mode_icons = [VAMode.HOLD, VAMode.CYCLE]

        # Remove all mode icons first
        await menu_manager.update_system_icons(entity_id, remove_icons=mode_icons)

        # Add current mode icon if it should be shown
        if new_mode in mode_icons:
            await menu_manager.update_system_icons(entity_id, add_icons=[new_mode])

        self.update_entity()

        if new_mode != VAMode.CYCLE:
            if self.cycle_view_task and not self.cycle_view_task.cancelled():
                self.cycle_view_task.cancel()
                _LOGGER.debug("Cycle display terminated")

        if new_mode == VAMode.NORMAL:
            # Add navigate to default view
            await self.async_browser_navigate(r.dashboard.home)
            _LOGGER.debug("NAVIGATE TO: %s", new_mode)

        elif new_mode == VAMode.MUSIC:
            # Add navigate to music view
            await self.async_browser_navigate(r.dashboard.music)
            _LOGGER.debug("NAVIGATE TO: %s", new_mode)

        elif new_mode == VAMode.CYCLE:
            # Add start cycle mode
            # Pull cycle_mode attribute
            self.cycle_view_task = self.hass.async_create_task(
                self.async_cycle_display_view(views=CYCLE_VIEWS)
            )
            _LOGGER.debug("START MODE: %s", new_mode)
        elif new_mode == VAMode.HOLD:
            # Hold mode, so cancel any revert timer
            self._cancel_display_revert_task()
