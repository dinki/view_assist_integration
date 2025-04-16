"""Handles entity listeners."""

import asyncio
from asyncio import Task
from datetime import datetime as dt
import logging
import random

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

from .const import (
    BROWSERMOD_DOMAIN,
    CC_CONVERSATION_ENDED_EVENT,
    CONF_DO_NOT_DISTURB,
    CYCLE_VIEWS,
    DEFAULT_VIEW_INFO,
    DOMAIN,
    REMOTE_ASSIST_DISPLAY_DOMAIN,
    USE_VA_NAVIGATION_FOR_BROWSERMOD,
    VA_ATTRIBUTE_UPDATE_EVENT,
    VA_BACKGROUND_UPDATE_EVENT,
    VAConfigEntry,
    VADisplayType,
    VAEvent,
    VAMode,
)
from .helpers import (
    async_get_download_image,
    async_get_filesystem_images,
    get_device_name_from_id,
    get_display_type_from_browser_id,
    ensure_menu_button_at_end,
    get_entity_attribute,
    get_entity_id_from_conversation_device_id,
    get_key,
    get_mute_switch_entity_id,
    get_revert_settings_for_mode,
    get_sensor_entity_from_instance,
    make_url_from_file_path,
)

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

        # Add microphone mute switch listener
        mute_switch = get_mute_switch_entity_id(
            hass, config_entry.runtime_data.mic_device
        )

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

        # Add mic mute switch listener
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
        if self.config_entry.runtime_data.rotate_background:
            # Set task based on mode
            if (
                self.config_entry.runtime_data.rotate_background_source
                == "link_to_entity"
            ):
                if self.config_entry.runtime_data.rotate_background_linked_entity:
                    _LOGGER.debug(
                        "Starting rotate background linked image listener for %s, linked to %s",
                        self.config_entry.runtime_data.name,
                        self.config_entry.runtime_data.rotate_background_linked_entity,
                    )
                    # Add listener for background changes
                    self.config_entry.async_on_unload(
                        self.hass.bus.async_listen(
                            VA_BACKGROUND_UPDATE_EVENT.format(
                                self.config_entry.runtime_data.rotate_background_linked_entity
                            ),
                            self.async_set_background_image,
                        )
                    )
                    # Set initial background from linked entity
                    await self.async_set_background_image(
                        get_entity_attribute(
                            self.hass,
                            self.config_entry.runtime_data.rotate_background_linked_entity,
                            "background",
                        )
                    )
                else:
                    _LOGGER.warning(
                        "%s is set to link its background image but no linked entity provided",
                        self.config_entry.runtime_data.name,
                    )
            else:
                _LOGGER.debug(
                    "Starting rotate background image task for %s",
                    self.config_entry.runtime_data.name,
                )
                self.rotate_background_task = (
                    self.config_entry.async_create_background_task(
                        self.hass,
                        self.async_background_image_rotation_task(),
                        f"{self.config_entry.runtime_data.name} rotate image task",
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
        await self.hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                "entity_id": entity_id,
                "current_path": path,
            },
        )

        # Do navigation and set revert if needed
        browser_id = get_device_name_from_id(
            self.hass, self.config_entry.runtime_data.display_device
        )
        display_type = get_display_type_from_browser_id(self.hass, browser_id)

        _LOGGER.debug(
            "Navigating: %s, browser_id: %s, path: %s, display_type: %s, mode: %s",
            self.config_entry.runtime_data.name,
            browser_id,
            path,
            display_type,
            self.config_entry.runtime_data.mode,
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
            self.config_entry.runtime_data.mode
        )
        revert_path = (
            getattr(self.config_entry.runtime_data, revert_view)
            if revert_view
            else None
        )

        # Set revert action if required
        if revert and path != revert_path:
            timeout = self.config_entry.runtime_data.view_timeout
            _LOGGER.debug("Adding revert to %s in %ss", revert_path, timeout)
            self.revert_view_task = self.hass.async_create_task(
                self._display_revert_delay(revert_path, timeout)
            )

    async def async_cycle_display_view(self, views: list[str]):
        """Cycle display."""

        view_index = 0
        _LOGGER.debug("Cycle display started")
        while self.config_entry.runtime_data.mode == VAMode.CYCLE:
            view_index = view_index % len(views)
            _LOGGER.debug("Cycling to view: %s", views[view_index])
            await self.async_browser_navigate(
                f"{self.config_entry.runtime_data.dashboard}/{views[view_index]}",
            )
            view_index += 1
            await asyncio.sleep(self.config_entry.runtime_data.view_timeout)

    async def async_background_image_rotation_task(self):
        """Task to get background image for image rotation."""
        source = self.config_entry.runtime_data.rotate_background_source
        path = self.config_entry.runtime_data.rotate_background_path
        interval = self.config_entry.runtime_data.rotate_background_interval
        image_index = 0

        # Clean path
        path.removeprefix("/").removesuffix("/")

        try:
            if source in ["local_sequence", "local_random"]:
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

                image_url = make_url_from_file_path(self.hass, image)
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
                self.config_entry.runtime_data.name,
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

    @callback
    def _async_on_mic_change(self, event: Event[EventStateChangedData]) -> None:
        mic_mute_new_state = event.data["new_state"].state

        # If not change to mic mute state, exit function
        if (
            not event.data.get("old_state")
            or event.data["old_state"].state == mic_mute_new_state
        ):
            return

        _LOGGER.debug("MIC MUTE: %s", mic_mute_new_state)
        status_icons = self.config_entry.runtime_data.status_icons.copy()

        if mic_mute_new_state == "on" and "mic" not in status_icons:
            status_icons.append("mic")
        elif mic_mute_new_state == "off" and "mic" in status_icons:
            status_icons.remove("mic")

        ensure_menu_button_at_end(status_icons)

        self.config_entry.runtime_data.status_icons = status_icons
        self.update_entity()

    @callback
    def _async_on_mediaplayer_device_mute_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
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
        status_icons = (
            self.config_entry.runtime_data.status_icons.copy()
            if self.config_entry.runtime_data.status_icons
            else []
        )

        if mp_mute_new_state and "mediaplayer" not in status_icons:
            status_icons.append("mediaplayer")
        elif not mp_mute_new_state and "mediaplayer" in status_icons:
            status_icons.remove("mediaplayer")

        ensure_menu_button_at_end(status_icons)

        self.config_entry.runtime_data.status_icons = status_icons
        self.update_entity()

    async def _async_cc_on_conversation_ended_handler(self, event: Event):
        """Handle custom conversation integration conversation ended event."""
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
            prefixes = ("light", "switch", "cover", "boolean", "input_boolean")

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
                await self.async_browser_navigate(self.config_entry.runtime_data.intent)
            else:
                word_count = len(speech_text.split())
                message_font_size = ["14vw", "8vw", "6vw", "4vw"][
                    min(word_count // 6, 3)
                ]
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
                await self.async_browser_navigate(
                    f"{self.config_entry.runtime_data.dashboard}/{DEFAULT_VIEW_INFO}"
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
        """Set dnd status icon."""

        # This is called from our set_service event listener and therefore event data is
        # slightly different.  See set_state_changed_attribute above
        dnd_new_state = event.data["new_value"]

        _LOGGER.debug("DND STATE: %s", dnd_new_state)
        status_icons = self.config_entry.runtime_data.status_icons.copy()
        if dnd_new_state and "dnd" not in status_icons:
            status_icons.append("dnd")
        elif not dnd_new_state and "dnd" in status_icons:
            status_icons.remove("dnd")

        ensure_menu_button_at_end(status_icons)

        self.config_entry.runtime_data.status_icons = status_icons
        self.update_entity()

    async def _async_on_mode_state_change(self, event: Event) -> None:
        """Set mode status icon."""

        new_mode = event.data["new_value"]

        _LOGGER.debug("MODE STATE: %s", new_mode)
        status_icons = self.config_entry.runtime_data.status_icons.copy()

        modes = [VAMode.HOLD, VAMode.CYCLE]

        # Remove all mode icons
        for mode in modes:
            if mode in status_icons:
                status_icons.remove(mode)

        # Now add back any you want
        if new_mode in modes and new_mode not in status_icons:
            status_icons.append(new_mode)

        ensure_menu_button_at_end(status_icons)

        self.config_entry.runtime_data.status_icons = status_icons
        self.update_entity()

        if new_mode != VAMode.CYCLE:
            if self.cycle_view_task and not self.cycle_view_task.cancelled():
                self.cycle_view_task.cancel()
                _LOGGER.debug("Cycle display terminated")

        if new_mode == VAMode.NORMAL:
            # Add navigate to default view
            await self.async_browser_navigate(self.config_entry.runtime_data.home)
            _LOGGER.debug("NAVIGATE TO: %s", new_mode)

        elif new_mode == VAMode.MUSIC:
            # Add navigate to music view
            await self.async_browser_navigate(self.config_entry.runtime_data.music)

            # --------------------------------------------
            # Service call option
            # --------------------------------------------
            # await self.hass.services.async_call(
            #     "switch",
            #     "turn_on",
            #     {
            #         "entity_id": "switch.android_satellite_viewassist_office_wyoming_mute"
            #     },
            # )

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
