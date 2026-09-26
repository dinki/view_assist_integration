"""Menu manager for View Assist."""

# TODO: Check icon ordering
# TODO: Add ability to allow entity: etc icons in status icons

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import slugify

from ..const import DEVICES, DOMAIN  # noqa: TID252
from ..helpers import get_config_entry_by_entity_id  # noqa: TID252
from ..typed import VAConfigEntry, VAEvent, VAEventType, VAMenuConfig  # noqa: TID252

_LOGGER = logging.getLogger(__name__)

StatusOrMenuItemsType = str | list[str]

ATTR_MENU = "menu"
ATTR_STATUS_ITEM = "status_item"
ATTR_TIMEOUT = "timeout"

TOGGLE_MENU_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("show", default=True): cv.boolean,
        vol.Optional("timeout"): vol.Any(int, None),
    }
)

ADD_ITEM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_STATUS_ITEM): vol.Any(str, [str]),
        vol.Optional(ATTR_MENU, default=False): cv.boolean,
        vol.Optional(ATTR_TIMEOUT): vol.Any(int, None),
    }
)
REMOVE_ITEM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_STATUS_ITEM): vol.Any(str, [str]),
        vol.Optional(ATTR_MENU, default=False): cv.boolean,
    }
)


class MenuManager:
    """Class to manage View Assist menus."""

    @classmethod
    def get(cls, hass: HomeAssistant, config: VAConfigEntry) -> MenuManager | None:
        """Get the instance for a config entry."""
        try:
            return hass.data[DOMAIN][DEVICES][config.entry_id][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize menu manager."""
        self.hass = hass
        self.config = config
        self.name = config.runtime_data.core.name

        self._item_remove_timeouts: dict[int, asyncio.Task] = {}
        self._menu_timeout_task: asyncio.Task | None = None

        self._internal_status_icons: list[str] = []
        self._internal_menu_items: list[str] = []
        self._menu_timeout: int = 10

        # NEW: Explicit tracking of runtime additions
        self._runtime_status_icons: list[str] = []
        self._runtime_menu_items: list[str] = []

        self.status_icons: list[str] = []
        self.menu_items: list[str] = []
        self.active: bool = False

    async def async_setup(self) -> bool:
        """Initialize menu manager for device."""

        d = self.config.runtime_data.dashboard.display_settings

        # Restore runtime additions from extra_data (if present)
        restored_runtime_status = self.config.runtime_data.extra_data.get("runtime_status_icons", [])
        restored_runtime_menu = self.config.runtime_data.extra_data.get("runtime_menu_items", [])

        if restored_runtime_status:
            self._runtime_status_icons = list(restored_runtime_status)
            _LOGGER.info(
                "Restored %d runtime status icons for %s",
                len(self._runtime_status_icons),
                self.name
            )
            # Clean up
            self.config.runtime_data.extra_data.pop("runtime_status_icons", None)

        if restored_runtime_menu:
            self._runtime_menu_items = list(restored_runtime_menu)
            _LOGGER.info(
                "Restored %d runtime menu items for %s",
                len(self._runtime_menu_items),
                self.name
            )
            # Clean up
            self.config.runtime_data.extra_data.pop("runtime_menu_items", None)

        # Build internal lists: config + runtime
        # Config icons first
        self._internal_status_icons = list(d.status_icons.copy())

        # Add runtime icons (avoid duplicates)
        for icon in self._runtime_status_icons:
            if icon not in self._internal_status_icons:
                self._internal_status_icons.append(icon)

        # Same for menu items
        self._internal_menu_items = list(d.menu_items.copy())

        for item in self._runtime_menu_items:
            if item not in self._internal_menu_items and item not in self._internal_status_icons:
                self._internal_menu_items.append(item)

        self._menu_timeout = d.menu_timeout
        self._build()

        return True

    async def async_setup_once(self) -> bool:
        """Set up menu manager services that should only be registered once."""
        MenuManagerServices(self.hass).register()
        return True

    async def async_unload(self) -> bool:
        """Stop the MenuManager."""
        return True

    async def async_unload_last(self):
        """Unload the last instance of MenuManager."""
        MenuManagerServices(self.hass).unregister()
        return True

    def add_items(
        self,
        items: StatusOrMenuItemsType,
        menu: bool = False,
        show_menu: bool = False,
        timeout: int | None = None,
    ) -> None:
        """Add status/menu items."""
        items = self.normalize_items(items)
        if isinstance(items, str):
            items = [items]
        elif not items:
            _LOGGER.warning("No valid items to add")
            return

        _LOGGER.debug("Adding items %s to %s", items, self.name)

        # Add items to menu or status items
        for item in items:
            self._add_remove_menu_item(
                item, True
            ) if menu else self._add_remove_status_item(item, True)

        # Handle item timeout for auto-remove
        if timeout:
            task_id = len(self._item_remove_timeouts) + 1
            task = self.config.async_create_background_task(
                self.hass,
                self._delayed_remove_items(task_id, items, menu, timeout),
                name=f"delayed_remove_items-{self.name}-{task_id}",
            )
            self._item_remove_timeouts[task_id] = task

        # Update menu and show if required
        self._build(show_menu)

    def remove_items(
        self,
        items: StatusOrMenuItemsType,
        menu: bool = False,
    ) -> None:
        """Remove status/menu icons."""
        items = self.normalize_items(items)
        if isinstance(items, str):
            items = [items]
        elif not items:
            _LOGGER.warning("No valid items to remove")
            return

        for item in items:
            self._add_remove_menu_item(
                item, False
            ) if menu else self._add_remove_status_item(item, False)

        self._build()

    @callback
    def toggle_menu(self, show: bool | None = None, timeout: int | None = None) -> None:
        """Toggle menu visibility for an entity."""
        # Check if menu is enabled
        if (
            self.config.runtime_data.dashboard.display_settings.menu_config
            == VAMenuConfig.DISABLED
        ):
            _LOGGER.warning("Menu is not enabled for %s", self.name)
            return

        # Cancel any existing timeout
        if self._menu_timeout_task and not self._menu_timeout_task.done():
            self._menu_timeout_task.cancel()

        # Determine new state
        _LOGGER.debug("Toggle menu for %s: show=%s", self.name, show)
        self.active = show if show is not None else not self.active

        # Notify
        self._notify_update()

        # Use config timeout if not set
        if not timeout:
            timeout = self._menu_timeout

        # Handle timeout for auto-close
        if self.active and timeout:
            self._menu_timeout_task = self.config.async_create_background_task(
                self.hass,
                self._menu_display_timeout_task(timeout),
                name=f"menu_timeout_{slugify(self.name)}",
            )

    def _build(self, show_menu: bool | None = None) -> None:
        """Update menu manager state based on changes from sensor entity."""

        # Status icons should be in reverse of order added to show right to left
        self.status_icons = self._internal_status_icons[::-1]

        # Filter icons that are also in status icons from menu items
        # Menu items should be in reverse order of added to show right to left
        self.menu_items = [
            item
            for item in self._internal_menu_items[::-1]
            if item not in self.status_icons
        ]

        # Save runtime tracking lists to extra_data for restoration
        # This allows runtime additions to persist through restarts
        self.config.runtime_data.extra_data["runtime_status_icons"] = self._runtime_status_icons
        self.config.runtime_data.extra_data["runtime_menu_items"] = self._runtime_menu_items

        if show_menu:
            self.toggle_menu(show_menu)
        else:
            # Update sensor attributes via dispatcher
            self._notify_update()
    def _notify_update(self) -> None:
        """Notify that an update has occurred."""

        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{self.config.entry_id}_event",
            VAEvent(
                VAEventType.CONFIG_UPDATE,
                {
                    "status_icons": self.status_icons,
                    "menu_items": self.menu_items,
                    "menu_active": self.active,
                },
            ),
        )

    def _add_remove_status_item(self, icon: str, add: bool) -> None:
        """Add or remove a status item."""
        if add:
            if icon not in self._internal_status_icons:
                self._internal_status_icons.append(icon)
                _LOGGER.debug("Added status icon %s to %s", icon, self.name)

            # Track as runtime addition (if not already tracked)
            if icon not in self._runtime_status_icons:
                self._runtime_status_icons.append(icon)
                _LOGGER.debug("Tracked %s as runtime status icon for %s", icon, self.name)
        else:
            # Remove from internal list
            if icon in self._internal_status_icons:
                self._internal_status_icons.remove(icon)
                _LOGGER.debug("Removed status icon %s from %s", icon, self.name)

            # Remove from runtime tracking
            if icon in self._runtime_status_icons:
                self._runtime_status_icons.remove(icon)
                _LOGGER.debug("Removed %s from runtime tracking for %s", icon, self.name)

    def _add_remove_menu_item(self, icon: str, add: bool) -> None:
        """Add or remove a menu item."""
        if add:
            if icon not in self._internal_menu_items:
                self._internal_menu_items.append(icon)
                _LOGGER.debug("Added menu item %s to %s", icon, self.name)

            # Track as runtime addition (if not already tracked)
            if icon not in self._runtime_menu_items:
                self._runtime_menu_items.append(icon)
                _LOGGER.debug("Tracked %s as runtime menu item for %s", icon, self.name)
        else:
            # Remove from internal list
            if icon in self._internal_menu_items:
                self._internal_menu_items.remove(icon)
                _LOGGER.debug("Removed menu item %s from %s", icon, self.name)

            # Remove from runtime tracking
            if icon in self._runtime_menu_items:
                self._runtime_menu_items.remove(icon)
                _LOGGER.debug("Removed %s from runtime tracking for %s", icon, self.name)

    async def _delayed_remove_items(
        self, task_id: int, items: StatusOrMenuItemsType, menu: bool, delay: int
    ) -> None:
        """Remove a status item after a delay."""
        await asyncio.sleep(delay)
        self.remove_items(items, menu)
        with contextlib.suppress(KeyError):
            del self._item_remove_timeouts[task_id]

    async def _menu_display_timeout_task(self, timeout: int) -> None:
        """Setup timeout for menu."""
        await asyncio.sleep(timeout)
        self.toggle_menu(False)

    def normalize_items(self, raw_input: Any) -> StatusOrMenuItemsType | None:
        """Normalize and validate status item input.

        Handles various input formats:
        - Single string
        - List of strings
        - JSON string representing a list
        - Dictionary with attributes

        Returns:
        - Single string
        - List of strings
        - None if invalid input
        """
        if raw_input is None:
            return None

        if isinstance(raw_input, str):
            if raw_input.startswith("[") and raw_input.endswith("]"):
                try:
                    parsed = json.loads(raw_input)
                    if isinstance(parsed, list):
                        string_items = [str(item) for item in parsed if item]
                        return string_items if string_items else None
                except json.JSONDecodeError:
                    return raw_input if raw_input else None
                else:
                    return None
            return raw_input if raw_input else None

        if isinstance(raw_input, list):
            string_items = [str(item) for item in raw_input if item]
            return string_items if string_items else None

        if isinstance(raw_input, dict):
            if "id" in raw_input:
                return str(raw_input["id"])
            if "name" in raw_input:
                return str(raw_input["name"])
            if "value" in raw_input:
                return str(raw_input["value"])

        return None


class MenuManagerServices:
    """Class to manage menu manager services."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the menu manager services."""
        self.hass = hass
        self.register()

    def register(self):
        """Register menu manager services."""
        self.hass.services.async_register(
            DOMAIN,
            "toggle_menu",
            self._handle_toggle_menu,
            schema=TOGGLE_MENU_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "add_status_item",
            self._handle_add_status_item,
            schema=ADD_ITEM_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "remove_status_item",
            self._handle_remove_status_item,
            schema=REMOVE_ITEM_SERVICE_SCHEMA,
        )

    def unregister(self):
        """Unregister menu manager services."""
        self.hass.services.async_remove(DOMAIN, "toggle_menu")
        self.hass.services.async_remove(DOMAIN, "add_status_item")
        self.hass.services.async_remove(DOMAIN, "remove_status_item")

    @callback
    def _handle_toggle_menu(self, call: ServiceCall):
        """Handle toggle menu service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        show = call.data.get("show", None)
        timeout = call.data.get(ATTR_TIMEOUT)

        if menu_manager := MenuManager.get(
            self.hass, get_config_entry_by_entity_id(self.hass, entity_id)
        ):
            menu_manager.toggle_menu(show, timeout=timeout)

    @callback
    def _handle_add_status_item(self, call: ServiceCall):
        """Handle add status item service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        raw_status_item = call.data.get(ATTR_STATUS_ITEM)
        menu = call.data.get(ATTR_MENU, False)
        timeout = call.data.get(ATTR_TIMEOUT)

        if menu_manager := MenuManager.get(
            self.hass, get_config_entry_by_entity_id(self.hass, entity_id)
        ):
            status_items = menu_manager.normalize_items(raw_status_item)
            if not status_items:
                raise ServiceValidationError("Invalid or empty status_item provided")
            menu_manager.add_items(items=status_items, menu=menu, timeout=timeout)

    @callback
    def _handle_remove_status_item(self, call: ServiceCall):
        """Handle remove status item service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        raw_status_item = call.data.get(ATTR_STATUS_ITEM)
        menu = call.data.get(ATTR_MENU, False)

        if menu_manager := MenuManager.get(
            self.hass, get_config_entry_by_entity_id(self.hass, entity_id)
        ):
            status_items = menu_manager.normalize_items(raw_status_item)
            if not status_items:
                raise ServiceValidationError("Invalid or empty status_item provided")
            menu_manager.remove_items(items=status_items, menu=menu)
