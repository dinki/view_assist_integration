"""Menu manager for View Assist."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union, Callable, TypedDict, cast

from homeassistant.core import HomeAssistant, State, callback

from .const import (
    CONF_ENABLE_MENU,
    CONF_ENABLE_MENU_TIMEOUT,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    CONF_SHOW_MENU_BUTTON,
    CONF_STATUS_ICON_SIZE,
    DEFAULT_ENABLE_MENU,
    DEFAULT_ENABLE_MENU_TIMEOUT,
    DEFAULT_MENU_ITEMS,
    DEFAULT_MENU_TIMEOUT,
    DEFAULT_SHOW_MENU_BUTTON,
    DOMAIN,
)
from .helpers import (
    get_config_entry_by_entity_id,
    get_sensor_entity_from_instance,
    ensure_menu_button_at_end,
    normalize_status_items,
)

_LOGGER = logging.getLogger(__name__)

StatusItemType = Union[str, List[str]]
MenuTargetType = Literal["status_icons", "menu_items"]


@dataclass
class MenuState:
    """Structured representation of a menu's state."""
    entity_id: str
    active: bool = False
    configured_items: List[str] = field(default_factory=list)
    status_icons: List[str] = field(default_factory=list)
    system_icons: List[str] = field(default_factory=list)
    menu_timeout: Optional[asyncio.Task] = None
    item_timeouts: Dict[str, asyncio.Task] = field(default_factory=dict)
    
    def activate(self, show_menu_button: bool = False) -> Dict[str, Any]:
        """Activate the menu and return changed attributes."""
        if self.active:
            return {}

        updated_icons = self.system_icons.copy()
        for item in self.configured_items:
            if item not in updated_icons:
                updated_icons.append(item)

        if show_menu_button:
            ensure_menu_button_at_end(updated_icons)

        self.active = True
        self.status_icons = updated_icons

        return {
            "status_icons": updated_icons,
            "menu_active": True,
        }

    def deactivate(self, show_menu_button: bool = False) -> Dict[str, Any]:
        """Deactivate the menu and return changed attributes."""
        if not self.active:
            return {}

        updated_icons = self.system_icons.copy()

        if show_menu_button and "menu" not in updated_icons:
            updated_icons.append("menu")

        self.active = False
        self.status_icons = updated_icons

        return {
            "status_icons": updated_icons, 
            "menu_active": False,
        }

    def add_items(self, items: List[str], target: MenuTargetType, show_menu_button: bool = False) -> Dict[str, Any]:
        """Add items to configured items or status icons and return changed attributes."""
        changes: Dict[str, Any] = {}

        if target == "menu_items":
            updated_items = self.configured_items.copy()
            changed = False

            for item in items:
                if item not in updated_items:
                    updated_items.append(item)
                    changed = True

            if changed:
                self.configured_items = updated_items
                changes["menu_items"] = updated_items

                if self.active:
                    icon_changes = self._rebuild_status_icons(show_menu_button)
                    if icon_changes:
                        changes.update(icon_changes)
        else:
            updated_icons = self.status_icons.copy()
            changed = False
            
            for item in items:
                if item != "menu" and item not in updated_icons:
                    updated_icons.append(item)
                    changed = True

            if show_menu_button and "menu" in updated_icons:
                updated_icons.remove("menu")
                updated_icons.append("menu")
                changed = True
            elif show_menu_button and "menu" not in updated_icons:
                updated_icons.append("menu")
                changed = True

            if changed:
                self.status_icons = updated_icons
                changes["status_icons"] = updated_icons

        return changes
    
    def remove_items(self, items: List[str], target: MenuTargetType, show_menu_button: bool = False) -> Dict[str, Any]:
        """Remove items from configured items or status icons and return changed attributes."""
        changes: Dict[str, Any] = {}

        if target == "menu_items":
            updated_items = [item for item in self.configured_items if item not in items]

            if updated_items != self.configured_items:
                self.configured_items = updated_items
                changes["menu_items"] = updated_items

                if self.active:
                    updated_icons = [icon for icon in self.status_icons if icon not in items]

                    if show_menu_button and "menu" not in updated_icons:
                        updated_icons.append("menu")

                    self.status_icons = updated_icons
                    changes["status_icons"] = updated_icons
        else:
            updated_icons = self.status_icons.copy()
            changed = False

            for item in items:
                if item == "menu" and show_menu_button:
                    continue

                if item in updated_icons:
                    updated_icons.remove(item)
                    changed = True

            if show_menu_button and "menu" not in updated_icons:
                updated_icons.append("menu")
                changed = True

            if changed:
                self.status_icons = updated_icons
                changes["status_icons"] = updated_icons

        return changes

    def _rebuild_status_icons(self, show_menu_button: bool = False) -> Dict[str, Any]:
        """Rebuild status icons based on current state."""
        updated_icons = self.system_icons.copy()

        for item in self.configured_items:
            if item not in updated_icons:
                updated_icons.append(item)

        if show_menu_button:
            ensure_menu_button_at_end(updated_icons)

        if updated_icons != self.status_icons:
            self.status_icons = updated_icons
            return {"status_icons": updated_icons}

        return {}


class MenuManager:
    """Class to manage View Assist menus."""

    def __init__(self, hass: HomeAssistant, config: Any) -> None:
        """Initialize menu manager."""
        self.hass = hass
        self.config = config
        self._menu_states: Dict[str, MenuState] = {}
        self._pending_updates: Dict[str, Dict[str, Any]] = {}
        self._update_task: Optional[asyncio.Task] = None

        self.hass.bus.async_listen_once("homeassistant_started", self._on_ha_started)

    async def _on_ha_started(self, event) -> None:
        """Initialize menu states after Home Assistant has started."""
        for entry_id in [entry.entry_id for entry in self.hass.config_entries.async_entries(DOMAIN)]:
            entity_id = get_sensor_entity_from_instance(self.hass, entry_id)
            if entity_id:
                await self.refresh_menu(entity_id)

    def _get_or_create_state(self, entity_id: str) -> MenuState:
        """Get or create a MenuState for the entity."""
        if entity_id not in self._menu_states:
            state = self.hass.states.get(entity_id)
            if state:
                configured_items = state.attributes.get("menu_items", []) or []
                status_icons = state.attributes.get("status_icons", []) or []
                is_active = state.attributes.get("menu_active", False)

                system_icons = [icon for icon in status_icons 
                                if icon not in set(configured_items) and icon != "menu"]

                self._menu_states[entity_id] = MenuState(
                    entity_id=entity_id,
                    active=is_active,
                    configured_items=configured_items,
                    status_icons=status_icons,
                    system_icons=system_icons,
                )
            else:
                self._menu_states[entity_id] = MenuState(entity_id=entity_id)

        return self._menu_states[entity_id]

    async def toggle_menu(self, entity_id: str, show: Optional[bool] = None, timeout: Optional[int] = None) -> None:
        """Toggle menu visibility for an entity."""
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry or not config_entry.options.get(CONF_ENABLE_MENU, DEFAULT_ENABLE_MENU):
            _LOGGER.debug("Menu not enabled or config not found for %s", entity_id)
            return

        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return

        menu_state = self._get_or_create_state(entity_id)

        current_active = menu_state.active
        if show is None:
            show = not current_active

        self._cancel_timeout(entity_id)

        show_menu_button = config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)

        changes = {}
        if show:
            current_view = self._get_current_view(state)
            if current_view and current_view in menu_state.configured_items:
                menu_state.configured_items.remove(current_view)
                    
            changes = menu_state.activate(show_menu_button)

            if timeout is not None:
                self._setup_timeout(entity_id, timeout)
            elif config_entry.options.get(CONF_ENABLE_MENU_TIMEOUT, DEFAULT_ENABLE_MENU_TIMEOUT):
                timeout_value = config_entry.options.get(CONF_MENU_TIMEOUT, DEFAULT_MENU_TIMEOUT)
                self._setup_timeout(entity_id, timeout_value)
        else:
            changes = menu_state.deactivate(show_menu_button)

        if changes:
            await self._update_entity_state(entity_id, changes)

    async def add_menu_item(self, entity_id: str, status_item: StatusItemType, menu: bool = False, timeout: Optional[int] = None) -> None:
        """Add status item(s) to the entity's status icons or menu items."""
        items = self._normalize_status_items(status_item)
        if not items:
            _LOGGER.warning("No valid items to add")
            return

        target = "menu_items" if menu else "status_icons"

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            _LOGGER.warning("No config entry found for entity %s", entity_id)
            return

        show_menu_button = config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)

        menu_state = self._get_or_create_state(entity_id)
        changes = menu_state.add_items(items, target, show_menu_button)

        if changes:
            await self._update_entity_state(entity_id, changes)

        if timeout is not None:
            for item in items:
                await self._setup_item_timeout(entity_id, item, timeout, target == "menu_items")

    async def remove_menu_item(self, entity_id: str, status_item: StatusItemType, menu: bool = False) -> None:
        """Remove status item(s) from the entity's status icons or menu items."""
        items = self._normalize_status_items(status_item)
        if not items:
            _LOGGER.warning("No valid items to remove")
            return

        target = "menu_items" if menu else "status_icons"

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            return

        show_menu_button = config_entry.options.get(CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON)

        menu_state = self._get_or_create_state(entity_id)
        changes = menu_state.remove_items(items, target, show_menu_button)

        if changes:
            await self._update_entity_state(entity_id, changes)

        for item in items:
            self._cancel_item_timeout(entity_id, item, target == "menu_items")

    async def refresh_menu(self, entity_id: str) -> None:
        """Refresh menu to ensure current view is filtered out."""
        state = self.hass.states.get(entity_id)
        if not state:
            return

        menu_state = self._get_or_create_state(entity_id)

        if not menu_state.active:
            return

        await self.toggle_menu(entity_id, True)

    def _get_current_view(self, state: State) -> Optional[str]:
        """Get the current view from a state object."""
        if current_path := state.attributes.get("current_path"):
            match = re.search(r"/view-assist/([^/]+)", current_path)
            if match:
                return match.group(1)

        display_device = state.attributes.get("display_device")
        if not display_device:
            return None

        for entity in self.hass.states.async_all():
            if entity.attributes.get("device_id") == display_device and (
            "path" in entity.entity_id or "browser" in entity.entity_id):
                path = entity.state or entity.attributes.get("path")
                if path and (match := re.search(r"/view-assist/([^/]+)", path)):
                    return match.group(1)

        return None

    def _setup_timeout(self, entity_id: str, timeout: int) -> None:
        """Setup timeout for menu."""
        menu_state = self._get_or_create_state(entity_id)
        
        if menu_state.menu_timeout and not menu_state.menu_timeout.done():
            menu_state.menu_timeout.cancel()

        menu_state.menu_timeout = self.hass.async_create_task(
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
        menu_state = self._get_or_create_state(entity_id)

        if menu_state.menu_timeout and not menu_state.menu_timeout.done():
            menu_state.menu_timeout.cancel()
            menu_state.menu_timeout = None

    async def _setup_item_timeout(self, entity_id: str, menu_item: str, timeout: int, is_menu_item: bool = False) -> None:
        """Set up a timeout for a specific menu item."""
        menu_state = self._get_or_create_state(entity_id)

        prefix = "menu_" if is_menu_item else "status_"
        item_key = f"{prefix}{menu_item}"

        self._cancel_item_timeout(entity_id, menu_item, is_menu_item)

        menu_state.item_timeouts[item_key] = self.hass.async_create_task(
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
        menu_state = self._get_or_create_state(entity_id)

        prefix = "menu_" if is_menu_item else "status_"
        item_key = f"{prefix}{menu_item}"

        if task := menu_state.item_timeouts.get(item_key):
            if not task.done():
                task.cancel()
            menu_state.item_timeouts.pop(item_key, None)

    async def _handle_timeout(self, callback: Callable, timeout: int) -> None:
        """Generic timeout handling for menu operations."""
        try:
            await asyncio.sleep(timeout)
            await callback()
        except asyncio.CancelledError:
            pass

    def _normalize_status_items(self, raw_input: Any) -> List[str]:
        """Normalize and validate status items input."""
        result = normalize_status_items(raw_input)

        if isinstance(result, str):
            return [result]
        elif result is None:
            return []
        return result

    async def _update_entity_state(self, entity_id: str, changes: Dict[str, Any]) -> None:
        """Update entity state with changes, batching updates when possible."""
        if not changes:
            return

        if entity_id not in self._pending_updates:
            self._pending_updates[entity_id] = {}

        self._pending_updates[entity_id].update(changes)

        if not self._update_task or self._update_task.done():
            self._update_task = self.hass.async_create_task(
                self._process_pending_updates()
            )

    async def _process_pending_updates(self) -> None:
        """Process all pending entity state updates."""
        await asyncio.sleep(0.01)

        updates = self._pending_updates.copy()
        self._pending_updates.clear()

        for entity_id, changes in updates.items():
            if not changes:
                continue

            changes["entity_id"] = entity_id

            await self.hass.services.async_call(
                DOMAIN,
                "set_state",
                changes,
            )
