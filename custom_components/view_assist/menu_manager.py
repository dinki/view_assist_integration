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
from .helpers import (
    get_config_entry_by_entity_id,
    get_sensor_entity_from_instance,
    ensure_menu_button_at_end,
)

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
        if not config_entry or not config_entry.options.get(CONF_ENABLE_MENU, DEFAULT_ENABLE_MENU):
            _LOGGER.debug("Menu not enabled or config not found for %s", entity_id)
            return

        current_state = self.hass.states.get(entity_id)
        if not current_state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return
            
        # Toggle if not specified
        current_active = current_state.attributes.get("menu_active", False)
        if show is None:
            show = not current_active
        
        # Cancel any existing timeout
        self._cancel_timeout(entity_id)
        self._active_menus[entity_id] = show
        
        # Get icons and config values
        current_icons = current_state.attributes.get("status_icons", []) or []
        config_items = config_entry.options.get(CONF_MENU_ITEMS, DEFAULT_MENU_ITEMS)
        show_menu_button = config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)
        
        # Process menu items
        try:
            items_to_use = list(config_items) if menu_items is None else list(menu_items)
        except (TypeError, ValueError):
            items_to_use = list(config_items)

        if show:
            # Get current view for filtering
            current_view = self._get_current_view(current_state)
            
            # Identify system icons to preserve
            all_menu_items = set(items_to_use)
            system_icons = [icon for icon in current_icons 
                        if icon not in all_menu_items and icon != "menu"]
            
            # Filter out current view from menu items
            menu_icons = [item for item in items_to_use if item != current_view]
            
            # Combine lists
            updated_icons = menu_icons + system_icons
            
            # Handle menu button
            if show_menu_button:
                ensure_menu_button_at_end(updated_icons)
            
            # Update entity
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": updated_icons,
                    "menu_active": True,
                },
            )
            
            # Set up timeout if needed
            if timeout is not None:
                self._setup_timeout(entity_id, timeout)
            elif config_entry.options.get(CONF_ENABLE_MENU_TIMEOUT, DEFAULT_ENABLE_MENU_TIMEOUT):
                timeout_value = config_entry.options.get(CONF_MENU_TIMEOUT, DEFAULT_MENU_TIMEOUT)
                self._setup_timeout(entity_id, timeout_value)
        else:
            # When hiding, remove all menu items but preserve system icons
            all_menu_items = set(items_to_use)
            system_icons = [icon for icon in current_icons 
                        if icon not in all_menu_items and icon != "menu"]
            
            updated_icons = system_icons
            
            # Add menu button if configured
            if show_menu_button:
                updated_icons.append("menu")
            
            # Update entity
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
        
        # Handle menu button position
        show_menu_button = config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)
        has_menu = "menu" in current_icons
        
        # Only add if not already present
        if menu_item not in current_icons:
            updated_icons = current_icons.copy()
            
            # If adding menu item and we have a menu button, ensure menu stays at end
            if menu_item != "menu":
                if has_menu:
                    # Remove menu button
                    updated_icons.remove("menu")
                    # Add new item
                    updated_icons.append(menu_item)
                    # Re-add menu button at end
                    updated_icons.append("menu")
                else:
                    # No menu button present, just add item
                    updated_icons.append(menu_item)
            elif show_menu_button:
                # We're adding a menu button, ensure it's at the end
                updated_icons.append(menu_item)
            
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
        # Check current_path attribute first
        if current_path := state.attributes.get("current_path"):
            match = re.search(r"/view-assist/([^/]+)", current_path)
            if match:
                return match.group(1)
        
        # Try to get from display device
        display_device = state.attributes.get("display_device")
        if not display_device:
            return None
            
        # Find browser or path entities for this device
        for entity in self.hass.states.async_all():
            if entity.attributes.get("device_id") == display_device and (
            "path" in entity.entity_id or "browser" in entity.entity_id):
                path = entity.state or entity.attributes.get("path")
                if path and (match := re.search(r"/view-assist/([^/]+)", path)):
                    return match.group(1)
        
        return None

    def _setup_timeout(self, entity_id: str, timeout: int) -> None:
        """Setup timeout for menu."""
        self._timeouts[entity_id] = self.hass.async_create_task(
            self._timeout_task(entity_id, timeout)
        )
    
    async def _timeout_task(self, entity_id: str, timeout: int) -> None:
        """Task to handle menu timeout."""
        await self._handle_timeout(
            lambda: self.toggle_menu(entity_id, False), 
            timeout
        )
        
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
        await self._handle_timeout(
            lambda: self.remove_menu_item(entity_id, menu_item),
            timeout
        )
    
    def _cancel_item_timeout(self, entity_id: str, menu_item: str) -> None:
        """Cancel timeout for a specific menu item."""
        item_key = f"{entity_id}_{menu_item}"
        if task := self._item_timeouts.pop(item_key, None):
            if not task.done():
                task.cancel()

    async def _handle_timeout(self, callback: callable, timeout: int) -> None:
        """Generic timeout handling for menu operations."""
        try:
            await asyncio.sleep(timeout)
            await callback()
        except asyncio.CancelledError:
            pass  # Normal when timeout is cancelled
