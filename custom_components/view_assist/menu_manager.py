"""Menu manager for View Assist."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant, State

from .const import (
    CONF_ENABLE_MENU,
    CONF_ENABLE_MENU_TIMEOUT,
    CONF_MENU_AUTO_CLOSE,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    CONF_SHOW_MENU_BUTTON,
    DEFAULT_ENABLE_MENU,
    DEFAULT_ENABLE_MENU_TIMEOUT,
    DEFAULT_MENU_AUTO_CLOSE,
    DEFAULT_MENU_ITEMS,
    DEFAULT_MENU_TIMEOUT,
    DEFAULT_SHOW_MENU_BUTTON,
    DOMAIN,
    VAConfigEntry,
)
from .helpers import get_config_entry_by_entity_id, get_sensor_entity_from_instance

_LOGGER = logging.getLogger(__name__)


class MenuManager:
    """Class to manage View Assist menus."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize menu manager."""
        self.hass = hass
        self.config = config
        self._active_menus: dict[str, bool] = {}  # Track open menus by entity_id
        self._timeouts: dict[str, asyncio.Task] = {}  # Track menu timeout timers
        self._item_timeouts: dict[str, asyncio.Task] = {}  # Track individual item timeout timers
        
        # Register for state changes to handle initialization
        self.hass.bus.async_listen_once("homeassistant_started", self._on_ha_started)

    async def _on_ha_started(self, event):
        """Initialize menu states after Home Assistant has started."""
        # Check all VA entities to ensure they don't have current view in menu
        for entry_id in [entry.entry_id for entry in self.hass.config_entries.async_entries(DOMAIN)]:
            entity_id = get_sensor_entity_from_instance(self.hass, entry_id)
            if entity_id:
                # Perform initial filtering of menu icons based on current view
                await self.refresh_menu(entity_id)

    async def toggle_menu(self, entity_id: str, show: bool = None, menu_items: list[str] = None, timeout: int = None) -> None:
        """Toggle menu visibility for an entity."""
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            _LOGGER.warning("No config entry found for entity %s", entity_id)
            return

        # Check if menu is enabled
        if not config_entry.options.get(CONF_ENABLE_MENU, False):
            _LOGGER.debug("Menu is not enabled for %s", entity_id)
            return

        # Get current state
        current_state = self.hass.states.get(entity_id)
        if not current_state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return
            
        # If show not specified, toggle based on current state
        current_active = current_state.attributes.get("menu_active", False)
        if show is None:
            show = not current_active
            
        _LOGGER.debug("Menu toggle for %s: current=%s, new=%s", entity_id, current_active, show)
        
        # Cancel any existing timeout
        self._cancel_timeout(entity_id)

        # Update menu state
        self._active_menus[entity_id] = show

        # Get current status icons and available menu items
        try:
            current_icons = current_state.attributes.get("status_icons", [])
            if not isinstance(current_icons, list):
                current_icons = []
        except (AttributeError, TypeError):
            current_icons = []
            
        config_items = config_entry.options.get(CONF_MENU_ITEMS, DEFAULT_MENU_ITEMS)
        
        # Check if permanent menu button is enabled
        show_menu_button = config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)
        
        # Handle various menu_items parameter types
        try:
            if menu_items is not None:
                # Special handling for empty dictionaries and empty lists
                if isinstance(menu_items, dict) and not menu_items:
                    items_to_use = list(config_items)
                elif not menu_items and not isinstance(menu_items, list):
                    items_to_use = list(config_items)
                else:
                    items_to_use = list(menu_items) if isinstance(menu_items, list) else []
            else:
                items_to_use = list(config_items)
        except (AttributeError, TypeError, ValueError) as ex:
            _LOGGER.warning("Error processing menu_items, using defaults: %s", ex)
            items_to_use = list(config_items)

        if show:
            # Get current view for filtering
            current_view = self._get_current_view(current_state)
            _LOGGER.debug("Current view for filtering: %s", current_view)
            
            # Identify system icons (not menu items) to preserve them
            # We need to know all possible menu items
            all_menu_items = set()
            try:
                all_menu_items.update(config_items)
                if menu_items and isinstance(menu_items, list):
                    all_menu_items.update(menu_items)
            except (TypeError, ValueError) as ex:
                _LOGGER.warning("Error building all_menu_items set: %s", ex)
                    
            # Get system icons (exclude all menu items and the menu button)
            system_icons = []
            try:
                system_icons = [icon for icon in current_icons 
                                if icon not in all_menu_items and icon != "menu"]
            except (TypeError, ValueError) as ex:
                _LOGGER.warning("Error filtering system icons: %s", ex)
                system_icons = []
            
            # Filter menu items to remove current view
            menu_icons = []
            for item in items_to_use:
                try:
                    # Skip if it's the current view
                    if current_view and item == current_view:
                        continue
                    menu_icons.append(item)
                except (TypeError, ValueError) as ex:
                    _LOGGER.warning("Error processing menu item %s: %s", item, ex)
            
            # Build final icon list: menu icons first, then system icons
            updated_icons = menu_icons + system_icons
            
            # Ensure the permanent menu button is present if configured
            if show_menu_button and "menu" not in updated_icons:
                updated_icons.append("menu")
            
            # Update entity with new status icons
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": updated_icons,
                    "menu_active": True,  # Explicitly set to True
                },
            )
            
            # Set up timeout if specified or configured
            if timeout is not None:
                self._setup_timeout(entity_id, timeout)
            elif config_entry.options.get(CONF_ENABLE_MENU_TIMEOUT, DEFAULT_ENABLE_MENU_TIMEOUT):
                timeout_value = config_entry.options.get(CONF_MENU_TIMEOUT, DEFAULT_MENU_TIMEOUT)
                self._setup_timeout(entity_id, timeout_value)
        else:
            # When hiding, remove all menu items but preserve system icons
            all_menu_items = set()
            try:
                all_menu_items.update(config_items)
                if menu_items and isinstance(menu_items, list):
                    all_menu_items.update(menu_items)
            except (TypeError, ValueError) as ex:
                _LOGGER.warning("Error building all_menu_items set: %s", ex)
                    
            # Get system icons (exclude all menu items and the menu button)
            system_icons = []
            try:
                system_icons = [icon for icon in current_icons 
                            if icon not in all_menu_items and icon != "menu"]
            except (TypeError, ValueError) as ex:
                _LOGGER.warning("Error filtering system icons: %s", ex)
                system_icons = []
            
            updated_icons = system_icons
            
            # Ensure the permanent menu button remains if configured
            if show_menu_button:
                updated_icons.append("menu")
            
            # Update entity with filtered status icons
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": updated_icons,
                    "menu_active": False,
                },
            )

    async def add_menu_item(self, entity_id: str, menu_item: str, timeout: int = None) -> None:
        """Add a single menu item to the entity's status icons."""
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            _LOGGER.warning("No config entry found for entity %s", entity_id)
            return

        # Get current state
        current_state = self.hass.states.get(entity_id)
        if not current_state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return
            
        # Get current status icons
        current_icons = current_state.attributes.get("status_icons", [])
        
        # Only add if not already present
        if menu_item not in current_icons:
            updated_icons = [menu_item] + current_icons
            
            # Update entity with new status icons
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": updated_icons,
                },
            )
        
        # Set up timeout for this item if specified
        if timeout is not None:
            await self._setup_item_timeout(entity_id, menu_item, timeout)

    async def remove_menu_item(self, entity_id: str, menu_item: str) -> None:
        """Remove a single menu item from the entity's status icons."""
        # Get current state
        current_state = self.hass.states.get(entity_id)
        if not current_state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return
            
        # Get current status icons
        current_icons = current_state.attributes.get("status_icons", [])
        
        # Only remove if present
        if menu_item in current_icons:
            # Special handling for menu button
            config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
            if menu_item == "menu" and config_entry and config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON):
                # Don't remove permanent menu button
                _LOGGER.debug("Skipping removal of permanent menu button")
                return
                
            updated_icons = [icon for icon in current_icons if icon != menu_item]
            
            # Update entity with new status icons
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": updated_icons,
                },
            )
        
        # Cancel any timeout for this item
        self._cancel_item_timeout(entity_id, menu_item)

    async def refresh_menu(self, entity_id: str) -> None:
        """Refresh menu to ensure current view is filtered out."""
        current_state = self.hass.states.get(entity_id)
        if not current_state:
            return
            
        # Only refresh if menu is active
        if not current_state.attributes.get("menu_active", False):
            return
            
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            return
            
        # Re-apply menu toggle with current items
        await self.toggle_menu(entity_id, True)

    def _get_current_view(self, state: State) -> str | None:
        """Get the current view from a state object."""
        # First check for current_path attribute
        if current_path := state.attributes.get("current_path"):
            match = re.search(r"/view-assist/([^/]+)", current_path)
            if match:
                return match.group(1)
        
        # Try to get from display device
        try:
            display_device = state.attributes.get("display_device")
            if not display_device:
                return None
                
            # Find browser or path entities for this device
            for entity in self.hass.states.async_all():
                if entity.attributes.get("device_id") == display_device:
                    if "path" in entity.entity_id or "browser" in entity.entity_id:
                        # Check pathSegments attribute
                        if path_segments := entity.attributes.get("pathSegments"):
                            if len(path_segments) > 2 and path_segments[1] == "view-assist":
                                return path_segments[2]
                                
                        # Try entity state or path attribute
                        if path := entity.state:
                            match = re.search(r"/view-assist/([^/]+)", path)
                            if match:
                                return match.group(1)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("Error determining current view: %s", ex)
        
        return None

    def _setup_timeout(self, entity_id: str, timeout: int) -> None:
        """Setup timeout for menu."""
        self._timeouts[entity_id] = self.hass.async_create_task(
            self._timeout_task(entity_id, timeout)
        )
    
    async def _timeout_task(self, entity_id: str, timeout: int) -> None:
        """Task to handle menu timeout."""
        try:
            await asyncio.sleep(timeout)
            _LOGGER.debug("Menu timeout triggered for %s", entity_id)
            await self.toggle_menu(entity_id, False)
        except asyncio.CancelledError:
            # Normal when timeout is cancelled
            pass
        
    def _cancel_timeout(self, entity_id: str) -> None:
        """Cancel any existing timeout for an entity."""
        if task := self._timeouts.pop(entity_id, None):
            if not task.done():
                task.cancel()

    async def _setup_item_timeout(self, entity_id: str, menu_item: str, timeout: int) -> None:
        """Set up a timeout for a specific menu item."""
        # Create a key for this item
        item_key = f"{entity_id}_{menu_item}"
        
        # Cancel existing timeout if any
        self._cancel_item_timeout(entity_id, menu_item)
        
        # Create new timeout task
        self._item_timeouts[item_key] = self.hass.async_create_task(
            self._item_timeout_task(entity_id, menu_item, timeout)
        )
    
    async def _item_timeout_task(self, entity_id: str, menu_item: str, timeout: int) -> None:
        """Task to handle individual menu item timeout."""
        try:
            await asyncio.sleep(timeout)
            _LOGGER.debug("Menu item timeout triggered for %s - %s", entity_id, menu_item)
            await self.remove_menu_item(entity_id, menu_item)
        except asyncio.CancelledError:
            # Normal when timeout is cancelled
            pass
    
    def _cancel_item_timeout(self, entity_id: str, menu_item: str) -> None:
        """Cancel timeout for a specific menu item."""
        item_key = f"{entity_id}_{menu_item}"
        if task := self._item_timeouts.pop(item_key, None):
            if not task.done():
                task.cancel()
