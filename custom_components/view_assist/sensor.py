"""VA Sensors."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime as dt
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.config_validation import make_entity_service_schema
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .devices.status import StatusManager
from .helpers import get_device_id_from_entity_id, get_mute_switch_entity_id
from .typed import (
    DISPLAY_DEVICE_TYPES,
    VAConfigEntry,
    VAEvent,
    VAEventType,
    VATimeFormat,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: VAConfigEntry, async_add_entities
):
    """Set up sensors from a config entry."""

    sensors = [ViewAssistSensor(hass, config_entry)]
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        name="set_state",
        schema=make_entity_service_schema({str: cv.match_all}, extra=vol.ALLOW_EXTRA),
        func="handle_set_entity_state",
    )

    async_add_entities(sensors)


class ViewAssistSensor(SensorEntity):
    """Representation of a View Assist Sensor."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        config: VAConfigEntry,
    ) -> None:
        """Initialise the sensor."""

        self.hass = hass
        self.config = config

        self._attr_name = config.runtime_data.core.name
        self._type = config.runtime_data.core.type
        self._attr_unique_id = f"{self._attr_name}_vasensor"
        self._attr_native_value = ""
        self._attr_icon = "mdi:glasses"
        self._attribute_listeners: dict[str, Callable] = {}
        self._last_update: dt = dt_util.now()

    async def async_added_to_hass(self) -> None:
        """Run when entity is about to be added to hass."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self.config.entry_id}_event",
                self._event_handler,
            )
        )

    async def _event_handler(self, event: VAEvent):
        """Handle internal events."""
        if event.event_name != VAEventType.STATUS_CHANGE:
            return

        _LOGGER.debug(
            "Handling sensor update event for %s",
            self.entity_id,
        )
        self.schedule_update_ha_state(True)

    async def handle_set_entity_state(self, **kwargs):
        """Set the state of the entity."""
        sm = StatusManager.get(self.hass, self.config)
        if sm:
            await sm.handle_set_state_action_call(kwargs)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity attributes."""
        # Core settings
        attrs = self._get_core_attributes()

        # All device settings
        attrs.update(self._get_all_device_status_attributes())

        # Display device settings
        if self._type in DISPLAY_DEVICE_TYPES:
            attrs.update(self._get_display_device_status_attributes())

        # Active overrides
        attrs["active_overrides"] = self._get_active_overrides_attributes()

        # Add extra_data attributes from runtime data
        # TODO: Reinstate this but remove current_path
        # attrs.update(self.config.runtime_data.extra_data)

        return attrs

    def _get_core_attributes(self) -> dict[str, Any]:
        """Build core attributes dictionary."""
        attrs = {}
        if sm := StatusManager.get(self.hass, self.config):
            attrs = asdict(sm.config.core)
            mic = sm.config.core.mic_device
            attrs["voice_device_id"] = get_device_id_from_entity_id(self.hass, mic)

        return attrs

    def _get_all_device_status_attributes(self) -> dict[str, Any]:
        """Build core status attributes dictionary."""
        attrs = {}
        if sm := StatusManager.get(self.hass, self.config):
            attrs["assist_state"] = sm.assist_state
            attrs["do_not_disturb"] = sm.do_not_disturb
            attrs["extra_data"] = sm.extra_data
            attrs["is_music_playing"] = sm.is_music_playing
            attrs["last_command"] = sm.last_command
            attrs["last_intent"] = sm.last_intent
            attrs["last_response"] = sm.last_response
            attrs["last_updated"] = dt_util.now().isoformat()
            attrs["media_album"] = sm.media_album
            attrs["media_artist"] = sm.media_artist
            attrs["media_content_type"] = sm.media_content_type
            attrs["media_track"] = sm.media_track
            attrs["muted"] = sm.muted
            attrs["use_announce"] = sm.config.default.use_announce
            attrs["mute_switch"] = (
                get_mute_switch_entity_id(self.hass, sm.config.core.mic_device),
            )
            attrs["alarm_sounding"] = sm.alarm_sounding
            attrs["changed_entities"] = sm.changed_entities
        return attrs

    def _get_display_device_status_attributes(self) -> dict[str, Any]:
        """Build display device status attributes dictionary."""
        # d = self.config.runtime_data
        attrs = {}
        if sm := StatusManager.get(self.hass, self.config):
            attrs["background"] = sm.background
            attrs["browser_connected"] = sm.browser_connected
            attrs["current_path"] = sm.current_path
            attrs["font_style"] = sm.config.dashboard.display_settings.font_style
            attrs["hold"] = sm.hold
            attrs["hold_view"] = sm.hold_view
            attrs["home"] = sm.config.dashboard.home
            attrs["menu_active"] = sm.menu_active
            attrs["menu_config"] = sm.config.dashboard.display_settings.menu_config
            attrs["menu_items"] = sm.menu_items.copy()
            attrs["mode"] = sm.mode
            attrs["screen_mode"] = sm.config.dashboard.display_settings.screen_mode
            attrs["status_icons"] = sm.status_icons.copy()
            attrs["status_icons_size"] = (
                sm.config.dashboard.display_settings.status_icons_size
            )
            attrs["use_24_hour_time"] = (
                sm.config.dashboard.display_settings.time_format == VATimeFormat.HOUR_24
            )
            attrs["view_timeout"] = sm.config.default.view_timeout
            attrs["weather_entity"] = sm.config.default.weather_entity
            if sm.view_data:
                attrs["view_data"] = sm.view_data
                attrs["title"] = sm.view_data.get("title")
                attrs["message"] = sm.view_data.get("message")
                attrs["message_font_size"] = sm.view_data.get("message_font_size")

        return attrs

    def _get_active_overrides_attributes(self) -> dict[str, Any]:
        """Build active runtime override attributes dictionary."""
        attrs = {}
        if sm := StatusManager.get(self.hass, self.config):
            if sm.extra_data:
                for attr in sm.extra_data:
                    attrs[attr] = sm.extra_data[attr]
        return attrs
