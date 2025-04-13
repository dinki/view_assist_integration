"""Integration services."""

from asyncio import TimerHandle
import json
import logging

import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_NAME, ATTR_TIME
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    selector,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .alarm_repeater import ALARMS, VAAlarmRepeater
from .const import (
    ATTR_BACKUP_CURRENT_VIEW,
    ATTR_COMMUNITY_VIEW,
    ATTR_DEVICE,
    ATTR_EVENT_DATA,
    ATTR_EVENT_NAME,
    ATTR_EXTRA,
    ATTR_INCLUDE_EXPIRED,
    ATTR_MAX_REPEATS,
    ATTR_MEDIA_FILE,
    ATTR_PATH,
    ATTR_REDOWNLOAD_FROM_REPO,
    ATTR_REMOVE_ALL,
    ATTR_RESUME_MEDIA,
    ATTR_TIMER_ID,
    ATTR_TYPE,
    DOMAIN,
    VAConfigEntry,
)
from .dashboard import (
    DASHBOARD_MANAGER,
    DashboardManager,
    DashboardManagerException,
    DownloadManagerException,
)
from .helpers import get_mimic_entity_id
from .timers import TIMERS, VATimers, decode_time_sentence

_LOGGER = logging.getLogger(__name__)


NAVIGATE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Required(ATTR_PATH): str,
    }
)

SET_TIMER_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
        vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
        vol.Required(ATTR_TYPE): str,
        vol.Optional(ATTR_NAME): str,
        vol.Required(ATTR_TIME): str,
        vol.Optional(ATTR_EXTRA): vol.Schema({}, extra=vol.ALLOW_EXTRA),
    }
)


CANCEL_TIMER_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_TIMER_ID, "target"): str,
        vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
        vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
        vol.Exclusive(ATTR_REMOVE_ALL, "target"): bool,
    }
)

SNOOZE_TIMER_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TIMER_ID): str,
        vol.Required(ATTR_TIME): str,
    }
)

GET_TIMERS_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_TIMER_ID, "target"): str,
        vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
        vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
        vol.Optional(ATTR_NAME): str,
        vol.Optional(ATTR_INCLUDE_EXPIRED, default=False): bool,
    }
)

ALARM_SOUND_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Required(ATTR_MEDIA_FILE): str,
        vol.Optional(ATTR_RESUME_MEDIA, default=True): bool,
        vol.Optional(ATTR_MAX_REPEATS, default=0): int,
    }
)

STOP_ALARM_SOUND_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
    }
)

BROADCAST_EVENT_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_EVENT_NAME): str,
        vol.Required(ATTR_EVENT_DATA): dict,
    }
)

DASHVIEW_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): str,
    }
)
LOAD_DASHVIEW_SERVICE_SCHEMA = DASHVIEW_SERVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_NAME): str,
        vol.Required(ATTR_REDOWNLOAD_FROM_REPO, default=False): bool,
        vol.Optional(ATTR_COMMUNITY_VIEW, default=False): bool,
        vol.Required(ATTR_BACKUP_CURRENT_VIEW, default=False): bool,
    }
)
TOGGLE_MENU_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("show", default=True): cv.boolean,
        vol.Optional("menu_items"): vol.Any(cv.ensure_list, None),
        vol.Optional("timeout"): vol.Any(int, None),
    }
)
ADD_MENU_ITEM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id, 
        vol.Required("menu_item"): vol.Any(str, [str]),
        vol.Optional("timeout"): vol.Any(int, None),
    }
)
REMOVE_MENU_ITEM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required("menu_item"): vol.Any(str, [str]),
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
            "set_timer",
            self.async_handle_set_timer,
            schema=SET_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "snooze_timer",
            self.async_handle_snooze_timer,
            schema=SNOOZE_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "cancel_timer",
            self.async_handle_cancel_timer,
            schema=CANCEL_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "get_timers",
            self.async_handle_get_timers,
            schema=GET_TIMERS_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

        self.hass.services.async_register(
            DOMAIN,
            "sound_alarm",
            self.async_handle_alarm_sound,
            schema=ALARM_SOUND_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "cancel_sound_alarm",
            self.async_handle_stop_alarm_sound,
            schema=STOP_ALARM_SOUND_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "broadcast_event",
            self.async_handle_broadcast_event,
            schema=BROADCAST_EVENT_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "load_view",
            self.async_handle_load_view,
            schema=LOAD_DASHVIEW_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "save_view",
            self.async_handle_save_view,
            schema=DASHVIEW_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "toggle_menu",
            self.async_handle_toggle_menu,
            schema=TOGGLE_MENU_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "add_menu_item",
            self.async_handle_add_menu_item,
            schema=ADD_MENU_ITEM_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "remove_menu_item",
            self.async_handle_remove_menu_item,
            schema=REMOVE_MENU_ITEM_SERVICE_SCHEMA,
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

    async def async_handle_alarm_sound(self, call: ServiceCall) -> ServiceResponse:
        """Handle alarm sound."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        media_file = call.data.get(ATTR_MEDIA_FILE)
        resume_media = call.data.get(ATTR_RESUME_MEDIA)
        max_repeats = call.data.get(ATTR_MAX_REPEATS)

        alarms: VAAlarmRepeater = self.hass.data[DOMAIN][ALARMS]
        return await alarms.alarm_sound(
            entity_id, media_file, "music", resume_media, max_repeats
        )

    async def async_handle_stop_alarm_sound(self, call: ServiceCall):
        """Handle stop alarm sound."""
        entity_id = call.data.get(ATTR_ENTITY_ID)

        alarms: VAAlarmRepeater = self.hass.data[DOMAIN][ALARMS]
        await alarms.cancel_alarm_sound(entity_id)

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
    # TIMERS
    # ----------------------------------------------------------------
    async def async_handle_set_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        timer_type = call.data.get(ATTR_TYPE)
        name = call.data.get(ATTR_NAME)
        timer_time = call.data.get(ATTR_TIME)
        extra_data = call.data.get(ATTR_EXTRA)

        sentence, timer_info = decode_time_sentence(timer_time)
        _LOGGER.debug("Time decode: %s -> %s", sentence, timer_info)
        if entity_id is None and device_id is None:
            mimic_device = get_mimic_entity_id(self.hass)
            if mimic_device:
                entity_id = mimic_device
                _LOGGER.warning(
                    "Using the set mimic entity %s to set timer as no entity or device id provided to the set timer service",
                    mimic_device,
                )
            else:
                raise vol.InInvalid("entity_id or device_id is required")

        extra_info = {"sentence": sentence}
        if extra_data:
            extra_info.update(extra_data)

        if timer_info:
            t: VATimers = self.hass.data[DOMAIN][TIMERS]
            timer_id, timer, response = await t.add_timer(
                timer_class=timer_type,
                device_or_entity_id=entity_id if entity_id else device_id,
                timer_info=timer_info,
                name=name,
                extra_info=extra_info,
            )

            return {"timer_id": timer_id, "timer": timer, "response": response}
        return {"error": "unable to decode time or interval information"}

    async def async_handle_snooze_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        timer_id = call.data.get(ATTR_TIMER_ID)
        timer_time = call.data.get(ATTR_TIME)

        _, timer_info = decode_time_sentence(timer_time)

        if timer_info:
            t: VATimers = self.hass.data[DOMAIN][TIMERS]
            timer_id, timer, response = await t.snooze_timer(
                timer_id,
                timer_info,
            )

            return {"timer_id": timer_id, "timer": timer, "response": response}
        return {"error": "unable to decode time or interval information"}

    async def async_handle_cancel_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        timer_id = call.data.get(ATTR_TIMER_ID)
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        cancel_all = call.data.get(ATTR_REMOVE_ALL, False)

        if any([timer_id, entity_id, device_id, cancel_all]):
            t: VATimers = self.hass.data[DOMAIN][TIMERS]
            result = await t.cancel_timer(
                timer_id=timer_id,
                device_or_entity_id=entity_id if entity_id else device_id,
                cancel_all=cancel_all,
            )
            return {"result": result}
        return {"error": "no attribute supplied"}

    async def async_handle_get_timers(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        timer_id = call.data.get(ATTR_TIMER_ID)
        name = call.data.get(ATTR_NAME)
        include_expired = call.data.get(ATTR_INCLUDE_EXPIRED, False)

        t: VATimers = self.hass.data[DOMAIN][TIMERS]
        result = t.get_timers(
            timer_id=timer_id,
            device_or_entity_id=entity_id if entity_id else device_id,
            name=name,
            include_expired=include_expired,
        )
        return {"result": result}

    # ----------------------------------------------------------------
    # VIEWS
    # ----------------------------------------------------------------
    async def async_handle_load_view(self, call: ServiceCall):
        """Handle load of a view from view_assist dir."""

        view_name = call.data.get(ATTR_NAME)
        download = call.data.get(ATTR_REDOWNLOAD_FROM_REPO, False)
        community_view = call.data.get(ATTR_COMMUNITY_VIEW, False)
        backup = call.data.get(ATTR_BACKUP_CURRENT_VIEW, False)

        dm: DashboardManager = self.hass.data[DOMAIN][DASHBOARD_MANAGER]
        try:
            if view_name == "dashboard":
                await dm.update_dashboard(download_from_repo=download)
            else:
                await dm.add_update_view(
                    name=view_name,
                    download_from_repo=download,
                    community_view=community_view,
                    backup_current_view=backup,
                )
        except (DownloadManagerException, DashboardManagerException) as ex:
            raise HomeAssistantError(ex) from ex

    async def async_handle_save_view(self, call: ServiceCall):
        """Handle saving view to view_assit dir."""

        view_name = call.data.get(ATTR_NAME)

        dm: DashboardManager = self.hass.data[DOMAIN][DASHBOARD_MANAGER]
        try:
            await dm.save_view(view_name)
        except (DownloadManagerException, DashboardManagerException) as ex:
            raise HomeAssistantError(ex) from ex

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
        menu_items = call.data.get("menu_items")
        timeout = call.data.get("timeout")
        
        # Convert empty dict to empty list if needed
        if isinstance(menu_items, dict) and not menu_items:
            menu_items = []

        menu_manager = self.hass.data[DOMAIN]["menu_manager"]
        await menu_manager.toggle_menu(entity_id, show, menu_items, timeout)

    async def async_handle_add_menu_item(self, call: ServiceCall):
        """Handle add menu item service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        menu_item = call.data.get("menu_item")
        timeout = call.data.get("timeout")

        # Support both single string and list of strings
        menu_items = menu_item
        if isinstance(menu_item, str):
            # Check if it's a list in string format
            if menu_item.startswith("[") and menu_item.endswith("]"):
                try:
                    menu_items = json.loads(menu_item)
                except json.JSONDecodeError:
                    menu_items = menu_item

        menu_manager = self.hass.data[DOMAIN]["menu_manager"]
        await menu_manager.add_menu_item(entity_id, menu_items, timeout)

    async def async_handle_remove_menu_item(self, call: ServiceCall):
        """Handle remove menu item service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        menu_item = call.data.get("menu_item")

        # Support both single string and list of strings
        menu_items = menu_item
        if isinstance(menu_item, str):
            # Check if it's a list in string format
            if menu_item.startswith("[") and menu_item.endswith("]"):
                try:
                    menu_items = json.loads(menu_item)
                except json.JSONDecodeError:
                    menu_items = menu_item

        menu_manager = self.hass.data[DOMAIN]["menu_manager"]
        await menu_manager.remove_menu_item(entity_id, menu_items)
