"""Integration services."""

from asyncio import TimerHandle
import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    selector,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .alarm_repeater import ALARMS, VAAlarmRepeater
from .const import (
    ATTR_DEVICE,
    ATTR_EVENT_DATA,
    ATTR_EVENT_NAME,
    ATTR_MAX_REPEATS,
    ATTR_MEDIA_FILE,
    ATTR_PATH,
    ATTR_RESUME_MEDIA,
    DOMAIN,
)
from .helpers import normalize_status_items
from .typed import VAConfigEntry

_LOGGER = logging.getLogger(__name__)


NAVIGATE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Required(ATTR_PATH): str,
    }
)


BROADCAST_EVENT_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_EVENT_NAME): str,
        vol.Required(ATTR_EVENT_DATA): dict,
    }
)

TOGGLE_MENU_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("show", default=True): cv.boolean,
        vol.Optional("timeout"): vol.Any(int, None),
    }
)

ADD_STATUS_ITEM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required("status_item"): vol.Any(str, [str]),
        vol.Optional("menu", default=False): cv.boolean,
        vol.Optional("timeout"): vol.Any(int, None),
    }
)
REMOVE_STATUS_ITEM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required("status_item"): vol.Any(str, [str]),
        vol.Optional("menu", default=False): cv.boolean,
    }
)

SET_AVATAR_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("avatar"): str,
    }
)
class VAServices:
    """Class to manage services."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config

        self.navigate_task: dict[str, TimerHandle] = {}

    async def async_setup_services(self):
        """Initialise VA services."""

        self.hass.services.async_register(
            DOMAIN,
            "navigate",
            self.async_handle_navigate,
            schema=NAVIGATE_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "broadcast_event",
            self.async_handle_broadcast_event,
            schema=BROADCAST_EVENT_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "toggle_menu",
            self.async_handle_toggle_menu,
            schema=TOGGLE_MENU_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "add_status_item",
            self.async_handle_add_status_item,
            schema=ADD_STATUS_ITEM_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "remove_status_item",
            self.async_handle_remove_status_item,
            schema=REMOVE_STATUS_ITEM_SERVICE_SCHEMA,
        )
        )
        self.hass.services.async_register(
            DOMAIN,
            "set_avatar",
            self.async_handle_set_avatar,
            schema=SET_AVATAR_SERVICE_SCHEMA,
        )
    # -----------------------------------------------------------------------
    # Get Target Satellite
    # Used to determine which VA satellite is being used based on its microphone device
    #
    # Sample usage
    # action: view_assist.get_target_satellite
    # data:
    #   device_id: 4385828338e48103f63c9f91756321df
    # -----------------------------------------------------------------------

    async def async_handle_broadcast_event(self, call: ServiceCall):
        """Fire an event with the provided name and data.

        name: View Assist Broadcast Event
        description: Immediately fires an event with the provided name and data
        """
        event_name = call.data.get(ATTR_EVENT_NAME)
        event_data = call.data.get(ATTR_EVENT_DATA, {})
        # Fire the event
        self.hass.bus.fire(event_name, event_data)

    # -----------------------------------------------------------------------
    # Handle Navigation
    # Used to determine how to change the view on the VA device
    #
    # action: view_assist.navigate
    # data:
    #   target_display_device: sensor.viewassist_office_browser_path
    #   path: /dashboard-viewassist/weather
    # ------------------------------------------------------------------------
    async def async_handle_navigate(self, call: ServiceCall):
        """Handle a navigate to view call."""

        va_entity_id = call.data.get(ATTR_DEVICE)
        path = call.data.get(ATTR_PATH)

        # get config entry from entity id to allow access to browser_id parameter
        entity_registry = er.async_get(self.hass)
        if va_entity := entity_registry.async_get(va_entity_id):
            entity_config_entry: VAConfigEntry = (
                self.hass.config_entries.async_get_entry(va_entity.config_entry_id)
            )

            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{entity_config_entry.entry_id}_browser_navigate",
                {"path": path},
            )

    # ----------------------------------------------------------------
    # MENU
    # ----------------------------------------------------------------
    async def async_handle_toggle_menu(self, call: ServiceCall):
        """Handle toggle menu service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        if not entity_id:
            _LOGGER.error("No entity_id provided in toggle_menu service call")
            return

        show = call.data.get("show", True)
        timeout = call.data.get("timeout")

        menu_manager = self.hass.data[DOMAIN]["menu_manager"]
        await menu_manager.toggle_menu(entity_id, show, timeout=timeout)

    async def async_handle_add_status_item(self, call: ServiceCall):
        """Handle add status item service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        if not entity_id:
            _LOGGER.error("No entity_id provided in add_status_item service call")
            return

        raw_status_item = call.data.get("status_item")
        menu = call.data.get("menu", False)
        timeout = call.data.get("timeout")

        status_items = normalize_status_items(raw_status_item)
        if not status_items:
            _LOGGER.error("Invalid or empty status_item provided")
            return

        menu_manager = self.hass.data[DOMAIN]["menu_manager"]
        await menu_manager.add_status_item(entity_id, status_items, menu, timeout)

    async def async_handle_remove_status_item(self, call: ServiceCall):
        """Handle remove status item service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        if not entity_id:
            _LOGGER.error("No entity_id provided in remove_status_item service call")
            return

        raw_status_item = call.data.get("status_item")
        menu = call.data.get("menu", False)

        status_items = normalize_status_items(raw_status_item)
        if not status_items:
            _LOGGER.error("Invalid or empty status_item provided")
            return

        menu_manager = self.hass.data[DOMAIN]["menu_manager"]
        await menu_manager.remove_status_item(entity_id, status_items, menu)

    async def async_handle_set_avatar(self, call: ServiceCall):
        """Handle changing the avatar for a device."""
        entity_ids = call.data.get(ATTR_ENTITY_ID)
        avatar = call.data.get("avatar")

        for entity_id in entity_ids:
            entity_registry = er.async_get(self.hass)
            if va_entity := entity_registry.async_get(entity_id):
                config_entry: VAConfigEntry = (
                    self.hass.config_entries.async_get_entry(va_entity.config_entry_id)
                )
                # update runtime_data
                config_entry.runtime_data.dashboard.display_settings.avatar_choice = avatar

                # notify frontend overlay to update
                async_dispatcher_send(
                    self.hass,
                    f"{DOMAIN}_{config_entry.entry_id}_event",
                    {
                        "state": "idle",
                        "style": config_entry.runtime_data.dashboard.display_settings.assist_prompt,
                        "avatar": avatar,
                    },
                )
