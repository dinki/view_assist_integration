"""Menu manager for View Assist."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Union, List, Callable, Literal, Optional, TypedDict, cast

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

# Type definitions
StatusItemType = Union[str, List[str]]
MenuTargetType = Literal["status_icons", "menu_items"]


class MenuItemOperation(TypedDict, total=False):
    """Type definition for menu item operations."""
    
    entity_id: str
    menu_item: StatusItemType
    target: MenuTargetType
    timeout: Optional[int]


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

    async def toggle_menu(self, entity_id: str, show: Optional[bool] = None, timeout: Optional[int] = None) -> None:
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
        
        # Get configured menu items from entity attributes or config options
        configured_menu_items = current_state.attributes.get("menu_items", []) or config_entry.options.get(CONF_MENU_ITEMS, DEFAULT_MENU_ITEMS)
        show_menu_button = config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)
        
        # Get system-only icons - these icons are not part of the menu system
        # and should always be visible regardless of menu state
        all_menu_items = set(configured_menu_items)
        system_only_icons = [icon for icon in current_icons 
                    if icon not in all_menu_items and icon != "menu"]
        
        if show:
            # Get current view for filtering
            current_view = self._get_current_view(current_state)
            
            # Filter out current view from menu items
            menu_icons = [item for item in configured_menu_items if item != current_view]
            
            # Combine lists: system icons + menu icons
            updated_icons = system_only_icons + menu_icons
            
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
            # When hiding, we only keep system icons (not menu icons)
            updated_icons = system_only_icons
            
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

    async def add_menu_item(self, entity_id: str, status_item: StatusItemType, to_menu_items: bool = False, timeout: Optional[int] = None) -> None:
        """Add status item(s) to the entity's status icons or menu items."""
        target = "menu_items" if to_menu_items else "status_icons"
        operation: MenuItemOperation = {
            "entity_id": entity_id,
            "menu_item": status_item,
            "target": target,
        }
        
        if timeout is not None:
            operation["timeout"] = timeout
            
        await self._manipulate_menu_items(operation, "add")

    async def remove_menu_item(self, entity_id: str, status_item: StatusItemType, from_menu_items: bool = False) -> None:
        """Remove status item(s) from the entity's status icons or menu items."""
        target = "menu_items" if from_menu_items else "status_icons"
        operation: MenuItemOperation = {
            "entity_id": entity_id,
            "menu_item": status_item,
            "target": target,
        }
            
        await self._manipulate_menu_items(operation, "remove")

    async def _manipulate_menu_items(self, operation: MenuItemOperation, action: Literal["add", "remove"]) -> None:
        """Generic method to add or remove menu items."""
        entity_id = operation["entity_id"]
        menu_item = operation["menu_item"]
        target = operation["target"]
        timeout = operation.get("timeout")
        
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry and action == "add":
            _LOGGER.warning("No config entry found for entity %s", entity_id)
            return

        # Get current state
        current_state = self.hass.states.get(entity_id)
        if not current_state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return
        
        # Convert single item to list for unified processing
        items_to_process = [menu_item] if isinstance(menu_item, str) else menu_item
        
        # Handle empty list
        if not items_to_process:
            return
            
        if target == "menu_items":
            # Working with the configured menu_items list
            existing_menu_items = current_state.attributes.get("menu_items", []) or []
            
            if action == "add":
                # Create updated menu items list
                updated_menu_items = list(existing_menu_items)
                
                # Add new items if not already present
                for item in items_to_process:
                    if item not in updated_menu_items:
                        updated_menu_items.append(item)
            else:  # remove
                # Filter out items that should be removed
                updated_menu_items = [item for item in existing_menu_items if item not in items_to_process]
            
            # Update entity with new menu items
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "menu_items": updated_menu_items,
                },
            )
            
            # If menu is currently visible, refresh it to show the changes
            if current_state.attributes.get("menu_active", False):
                if action == "add":
                    await self.refresh_menu(entity_id)
                else:  # remove
                    # Only remove the specific items we want to remove from status_icons
                    current_icons = current_state.attributes.get("status_icons", [])
                    updated_icons = [icon for icon in current_icons if icon not in items_to_process]
                    
                    await self.hass.services.async_call(
                        DOMAIN,
                        "set_state",
                        {
                            "entity_id": entity_id,
                            "status_icons": updated_icons,
                        },
                    )
        else:  # status_icons
            # Get current status icons
            current_icons = current_state.attributes.get("status_icons", [])
            show_menu_button = config_entry and config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)
            has_menu = "menu" in current_icons
            
            # Remove menu button if present (we'll add it back at the end if needed)
            updated_icons = [icon for icon in current_icons if icon != "menu"]
            
            if action == "add":
                # Add new items if not already present
                for item in items_to_process:
                    if item != "menu" and item not in updated_icons:
                        updated_icons.append(item)
            else:  # remove
                # Filter items considering permanent menu button
                filtered_items = []
                for item in items_to_process:
                    # Skip permanent menu button
                    if item == "menu" and show_menu_button:
                        _LOGGER.debug("Skipping removal of permanent menu button")
                        continue
                    filtered_items.append(item)
                
                # Remove the items
                updated_icons = [icon for icon in updated_icons if icon not in filtered_items]
            
            # Add menu button at the end if it was present or is required
            if has_menu or (show_menu_button and "menu" not in updated_icons):
                updated_icons.append("menu")
            
            # Update entity with new status icons
            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                {
                    "entity_id": entity_id,
                    "status_icons": updated_icons,
                },
            )
        
        # Handle timeouts
        if action == "add" and timeout is not None:
            for item in items_to_process:
                await self._setup_item_timeout(entity_id, item, timeout, target == "menu_items")
        elif action == "remove":
            # Cancel any timeouts for these items
            for item in items_to_process:
                self._cancel_item_timeout(entity_id, item, target == "menu_items")

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

    def _get_current_view(self, state: State) -> Optional[str]:
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

    async def _setup_item_timeout(self, entity_id: str, menu_item: str, timeout: int, is_menu_item: bool = False) -> None:
        """Set up a timeout for a specific menu item."""
        # Create a key for this item - include a prefix to differentiate between menu_items and status_icons
        prefix = "menu_" if is_menu_item else "status_"
        item_key = f"{prefix}{entity_id}_{menu_item}"
        
        # Cancel existing timeout if any
        self._cancel_item_timeout(entity_id, menu_item, is_menu_item)
        
        # Create new timeout task
        self._item_timeouts[item_key] = self.hass.async_create_task(
            self._item_timeout_task(entity_id, menu_item, timeout, is_menu_item)
        )
    
    async def _item_timeout_task(self, entity_id: str, menu_item: str, timeout: int, is_menu_item: bool = False) -> None:
        """Task to handle individual menu item timeout."""
        await self._handle_timeout(
            lambda: self.remove_menu_item(entity_id, menu_item, is_menu_item),
            timeout
        )
    
    def _cancel_item_timeout(self, entity_id: str, menu_item: str, is_menu_item: bool = False) -> None:
        """Cancel timeout for a specific menu item."""
        prefix = "menu_" if is_menu_item else "status_"
        item_key = f"{prefix}{entity_id}_{menu_item}"
        if task := self._item_timeouts.pop(item_key, None):
            if not task.done():
                task.cancel()

    async def _handle_timeout(self, callback: Callable, timeout: int) -> None:
        """Generic timeout handling for menu operations."""
        try:
            await asyncio.sleep(timeout)
            await callback()
        except asyncio.CancelledError:
            pass  # Normal when timeout is cancelled
