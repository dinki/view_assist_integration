"""VA Sensors."""

import asyncio
from collections.abc import Callable
from datetime import datetime as dt
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.config_validation import make_entity_service_schema
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, OPTION_KEY_MIGRATIONS
from .core import TimerManager
from .devices import MenuManager, NavigationManager
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
        self._last_update: dt = dt.now()

    async def async_added_to_hass(self) -> None:
        """Run when entity is about to be added to hass."""

        # Add internal event listeners
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_event",
                self._event_handler,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self.config.entry_id}_event",
                self._event_handler,
            )
        )

        # Add listener to timer changes
        # if timers := TimerManager.get(self.hass):
        #    timers.store.add_listener(self.entity_id, self._event_handler)

    async def _event_handler(self, event: VAEvent):
        """Handle internal events."""
        if isinstance(event, VAEvent):
            # Add small delay before updating sensor entity to force card
            # to refresh after viewassist object created on browser window
            if event.event_name == VAEventType.BROWSER_REGISTERED:
                await asyncio.sleep(0.5)

            _LOGGER.debug(
                "Handling event %s received for %s", event.event_name, self.entity_id
            )

            if event.event_name in [
                VAEventType.BACKGROUND_CHANGE,
                VAEventType.TIMER_UPDATE,
                VAEventType.BROWSER_REGISTERED,
                VAEventType.CONFIG_UPDATE,
            ]:
                self.schedule_update_ha_state(True)

    @callback
    def handle_set_entity_state(self, **kwargs):
        """Set the state of the entity."""
        update_ha = False
        for k, v in kwargs.items():
            _LOGGER.debug("Setting %s to %s for %s", k, v, self.entity_id)
            if k == "entity_id":
                continue
            if k == "allow_create":
                continue
            if k == "state":
                self._attr_native_value = v
                continue

            # Specific overrides
            if hasattr(self.config.runtime_data.runtime_config_overrides, k):
                if getattr(self.config.runtime_data.runtime_config_overrides, k) != v:
                    setattr(self.config.runtime_data.runtime_config_overrides, k, v)
                    update_ha = True
                continue

            # Set the value of named vartiables or add/update to extra_data dict
            if hasattr(self.config.runtime_data.default, k):
                if getattr(self.config.runtime_data.default, k) != v:
                    setattr(self.config.runtime_data.default, k, v)
                    update_ha = True
            elif self.config.runtime_data.extra_data.get(k) != v:
                self.config.runtime_data.extra_data[k] = v
                update_ha = True

        if update_ha:
            self.schedule_update_ha_state(True)

    # TODO: Remove this when BPs/Views migrated
    def get_option_key_migration_value(self, value: str) -> str:
        """Get the original option key for a given new option key."""
        for key, key_value in OPTION_KEY_MIGRATIONS.items():
            if key_value == value:
                return key
        return value

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
        attrs.update(self.config.runtime_data.extra_data)

        return attrs

    def _get_core_attributes(self) -> dict[str, Any]:
        """Build core attributes dictionary."""
        d = self.config.runtime_data.core
        return {
            "name": d.name,
            "type": d.type,
            "mic_device": d.mic_device,
            "mic_device_id": get_device_id_from_entity_id(self.hass, d.mic_device),
            "mute_switch": get_mute_switch_entity_id(self.hass, d.mic_device),
            "display_device": d.display_device,
            "intent_device": d.intent_device,
            "orientation_sensor": d.orientation_sensor,
            "mediaplayer_device": d.mediaplayer_device,
            "musicplayer_device": d.musicplayer_device,
            "voice_device_id": get_device_id_from_entity_id(self.hass, d.mic_device),
        }

    def _get_all_device_status_attributes(self) -> dict[str, Any]:
        """Build core status attributes dictionary."""
        d = self.config.runtime_data.default

        tm = TimerManager.get(self.hass)
        timers = tm.get_timers(entity_id=self.entity_id)
        return {
            "last_updated": dt.now().isoformat(),
            "do_not_disturb": d.do_not_disturb,
            "use_announce": d.use_announce,
            "timers": timers,
        }

    def _get_display_device_status_attributes(self) -> dict[str, Any]:
        """Build display device status attributes dictionary."""
        d = self.config.runtime_data
        mm = MenuManager.get(self.hass, self.config)

        return {
            "status_icons": mm.status_icons.copy() if mm else [],
            "status_icons_size": d.dashboard.display_settings.status_icons_size,
            "menu_config": d.dashboard.display_settings.menu_config,
            "menu_items": mm.menu_items.copy() if mm else [],
            "menu_active": mm.active if mm else False,
            "font_style": d.dashboard.display_settings.font_style,
            "use_24_hour_time": d.dashboard.display_settings.time_format
            == VATimeFormat.HOUR_24,
            "background": d.dashboard.background_settings.background,
            "mode": d.default.mode,
            "view_timeout": d.default.view_timeout,
            "weather_entity": d.default.weather_entity,
        }

    def _get_active_overrides_attributes(self) -> dict[str, Any]:
        """Build active runtime override attributes dictionary."""
        d = self.config.runtime_data.runtime_config_overrides
        attrs = {}
        if d.home is not None and d.home != "":
            attrs["home"] = d.home
        if d.assist_prompt is not None and d.assist_prompt != "":
            attrs["assist_prompt"] = d.assist_prompt
        return attrs
