"""Menu manager for View Assist."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Dict, List, Optional, Tuple, Union

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_ENABLE_MENU,
    CONF_ENABLE_MENU_TIMEOUT,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    CONF_SHOW_MENU_BUTTON,
    DEFAULT_ENABLE_MENU,
    DEFAULT_ENABLE_MENU_TIMEOUT,
    DEFAULT_MENU_ITEMS,
    DEFAULT_MENU_TIMEOUT,
    DEFAULT_SHOW_MENU_BUTTON,
    DOMAIN,
    VAConfigEntry,
    VAEvent,
)
from .helpers import (
    ensure_menu_button_at_end,
    get_config_entry_by_entity_id,
    get_sensor_entity_from_instance,
    normalize_status_items,
)

_LOGGER = logging.getLogger(__name__)

StatusItemType = Union[str, List[str]]


@dataclass
class MenuState:
    """Structured representation of a menu's state."""

    entity_id: str
    active: bool = False
    configured_items: List[str] = field(default_factory=list)
    status_icons: List[str] = field(default_factory=list)
    system_icons: List[str] = field(default_factory=list)
    menu_timeout: Optional[asyncio.Task] = None
    item_timeouts: Dict[Tuple[str, str, bool], asyncio.Task] = field(
        default_factory=dict
    )


class MenuManager:
    """Class to manage View Assist menus."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize menu manager."""
        self.hass = hass
        self.config = config
        self._menu_states: Dict[str, MenuState] = {}
        self._pending_updates: Dict[str, Dict[str, any]] = {}
        self._update_event = asyncio.Event()
        self._update_task: Optional[asyncio.Task] = None

        config.async_on_unload(self.cleanup)

        self.hass.bus.async_listen_once(
            "homeassistant_started", self._on_ha_started)

    async def _on_ha_started(self, event: Event) -> None:
        """Initialize menu states and start processor after Home Assistant has started."""
        for entry_id in [
            entry.entry_id for entry in self.hass.config_entries.async_entries(DOMAIN)
        ]:
            entity_id = get_sensor_entity_from_instance(self.hass, entry_id)
            if entity_id:
                self._get_or_create_state(entity_id)

        self._update_task = self.config.async_create_background_task(
            self.hass,
            self._update_processor(),
            name="VA Menu Manager",
        )

    def _get_or_create_state(self, entity_id: str) -> MenuState:
        """Get or create a MenuState for the entity."""
        if entity_id not in self._menu_states:
            self._menu_states[entity_id] = MenuState(entity_id=entity_id)

            state = self.hass.states.get(entity_id)
            if state:
                menu_items = state.attributes.get(
                    CONF_MENU_ITEMS, DEFAULT_MENU_ITEMS)
                self._menu_states[entity_id].configured_items = (
                    menu_items if menu_items else []
                )

                status_icons = state.attributes.get("status_icons", [])
                self._menu_states[entity_id].status_icons = (
                    status_icons if status_icons else []
                )

                self._menu_states[entity_id].active = state.attributes.get(
                    "menu_active", False
                )

                self._menu_states[entity_id].system_icons = [
                    icon
                    for icon in self._menu_states[entity_id].status_icons
                    if icon not in self._menu_states[entity_id].configured_items
                    and icon != "menu"
                ]

        return self._menu_states[entity_id]

    async def toggle_menu(
        self, entity_id: str, show: Optional[bool] = None, timeout: Optional[int] = None
    ) -> None:
        """Toggle menu visibility for an entity."""
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry or not config_entry.options.get(
            CONF_ENABLE_MENU, DEFAULT_ENABLE_MENU
        ):
            _LOGGER.debug(
                "Menu not enabled or config not found for %s", entity_id)
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

        show_menu_button = config_entry.options.get(
            CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON
        )

        changes = {}

        if show:

            system_icons = [
                icon
                for icon in menu_state.status_icons
                if icon not in menu_state.configured_items and icon != "menu"
            ]

            updated_icons = system_icons.copy()
            for item in menu_state.configured_items:
                if item not in updated_icons:
                    updated_icons.append(item)

            if show_menu_button:
                ensure_menu_button_at_end(updated_icons)

            menu_state.active = True
            menu_state.status_icons = updated_icons

            changes = {"status_icons": updated_icons, "menu_active": True}

            if timeout is not None:
                self._setup_timeout(entity_id, timeout)
            elif config_entry.options.get(
                CONF_ENABLE_MENU_TIMEOUT, DEFAULT_ENABLE_MENU_TIMEOUT
            ):
                timeout_value = config_entry.options.get(
                    CONF_MENU_TIMEOUT, DEFAULT_MENU_TIMEOUT
                )
                self._setup_timeout(entity_id, timeout_value)
        else:
            updated_icons = menu_state.system_icons.copy()

            if show_menu_button and "menu" not in updated_icons:
                updated_icons.append("menu")

            menu_state.active = False
            menu_state.status_icons = updated_icons

            changes = {"status_icons": updated_icons, "menu_active": False}

        if changes:
            await self._update_entity_state(entity_id, changes)

            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{config_entry.entry_id}_event",
                VAEvent("menu_update", {"menu_active": show}),
            )

    async def add_menu_item(
        self,
        entity_id: str,
        status_item: StatusItemType,
        menu: bool = False,
        timeout: Optional[int] = None,
    ) -> None:
        """Add status item(s) to the entity's status icons or menu items."""
        items = normalize_status_items(status_item)
        if isinstance(items, str):
            items = [items]
        elif items is None:
            items = []

        if not items:
            _LOGGER.warning("No valid items to add")
            return

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            _LOGGER.warning("No config entry found for entity %s", entity_id)
            return

        menu_state = self._get_or_create_state(entity_id)
        show_menu_button = config_entry.options.get(
            CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON
        )
        changes = {}

        if menu:
            updated_items = menu_state.configured_items.copy()
            changed = False

            for item in items:
                if item not in updated_items:
                    updated_items.append(item)
                    changed = True

            if changed:
                menu_state.configured_items = updated_items
                changes["menu_items"] = updated_items

                if menu_state.active:
                    updated_icons = menu_state.status_icons.copy()

                    for item in items:
                        if item not in updated_icons:
                            updated_icons.append(item)

                    if show_menu_button:
                        ensure_menu_button_at_end(updated_icons)

                    menu_state.status_icons = updated_icons
                    changes["status_icons"] = updated_icons
        else:
            updated_icons = menu_state.status_icons.copy()
            changed = False

            for item in items:
                if item != "menu" and item not in updated_icons:
                    updated_icons.append(item)
                    changed = True

            if show_menu_button:
                ensure_menu_button_at_end(updated_icons)
                changed = True

            if changed:
                menu_state.status_icons = updated_icons
                changes["status_icons"] = updated_icons

        if changes:
            await self._update_entity_state(entity_id, changes)

        if timeout is not None:
            for item in items:
                await self._setup_item_timeout(entity_id, item, timeout, menu)

    async def remove_menu_item(
        self, entity_id: str, status_item: StatusItemType, from_menu: bool = False
    ) -> None:
        """Remove status item(s) from the entity's status icons or menu items."""
        items = normalize_status_items(status_item)
        if isinstance(items, str):
            items = [items]
        elif items is None:
            items = []

        if not items:
            _LOGGER.warning("No valid items to remove")
            return

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            return

        menu_state = self._get_or_create_state(entity_id)
        show_menu_button = config_entry.options.get(
            CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON
        )
        changes = {}

        if from_menu:
            updated_items = [
                item for item in menu_state.configured_items if item not in items
            ]

            if updated_items != menu_state.configured_items:
                menu_state.configured_items = updated_items
                changes["menu_items"] = updated_items

                if menu_state.active:
                    updated_icons = menu_state.status_icons.copy()

                    for item in items:
                        if item in updated_icons:
                            updated_icons.remove(item)

                    if show_menu_button and "menu" not in updated_icons:
                        updated_icons.append("menu")

                    menu_state.status_icons = updated_icons
                    changes["status_icons"] = updated_icons
        else:
            updated_icons = menu_state.status_icons.copy()
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
                menu_state.status_icons = updated_icons
                changes["status_icons"] = updated_icons

        if changes:
            await self._update_entity_state(entity_id, changes)

        for item in items:
            self._cancel_item_timeout(entity_id, item, from_menu)

    def _setup_timeout(self, entity_id: str, timeout: int) -> None:
        """Setup timeout for menu."""
        menu_state = self._get_or_create_state(entity_id)

        if menu_state.menu_timeout and not menu_state.menu_timeout.done():
            menu_state.menu_timeout.cancel()
            menu_state.menu_timeout = None

        async def _timeout_task():
            try:
                await asyncio.sleep(timeout)
                await self.toggle_menu(entity_id, False)
            except asyncio.CancelledError:
                pass

        menu_state.menu_timeout = self.config.async_create_background_task(
            self.hass,
            _timeout_task(),
            name=f"VA Menu Timeout {entity_id}",
        )

    def _cancel_timeout(self, entity_id: str) -> None:
        """Cancel any existing timeout for an entity."""
        if entity_id not in self._menu_states:
            return

        menu_state = self._menu_states[entity_id]

        if menu_state.menu_timeout and not menu_state.menu_timeout.done():
            menu_state.menu_timeout.cancel()
            menu_state.menu_timeout = None

    async def _setup_item_timeout(
        self, entity_id: str, menu_item: str, timeout: int, is_menu_item: bool = False
    ) -> None:
        """Set up a timeout for a specific menu item."""
        menu_state = self._get_or_create_state(entity_id)

        item_key = (entity_id, menu_item, is_menu_item)

        self._cancel_item_timeout(entity_id, menu_item, is_menu_item)

        async def _item_timeout_task():
            try:
                await asyncio.sleep(timeout)
                await self.remove_menu_item(entity_id, menu_item, is_menu_item)
            except asyncio.CancelledError:
                pass

        menu_state.item_timeouts[item_key] = self.config.async_create_background_task(
            self.hass,
            _item_timeout_task(),
            name=f"VA Item Timeout {entity_id} {menu_item}",
        )

    def _cancel_item_timeout(
        self, entity_id: str, menu_item: str, is_menu_item: bool = False
    ) -> None:
        """Cancel timeout for a specific menu item."""
        if entity_id not in self._menu_states:
            return

        menu_state = self._menu_states[entity_id]

        item_key = (entity_id, menu_item, is_menu_item)

        if (
            item_key in menu_state.item_timeouts
            and not menu_state.item_timeouts[item_key].done()
        ):
            menu_state.item_timeouts[item_key].cancel()
            menu_state.item_timeouts.pop(item_key)

    async def _update_entity_state(
        self, entity_id: str, changes: Dict[str, any]
    ) -> None:
        """Update entity state with changes, batching updates when possible."""
        if not changes:
            return

        if entity_id not in self._pending_updates:
            self._pending_updates[entity_id] = {}

        self._pending_updates[entity_id].update(changes)

        self._update_event.set()

    async def _update_processor(self) -> None:
        """Process updates in an event-driven way."""
        while True:
            try:
                await self._update_event.wait()
                self._update_event.clear()

                updates = self._pending_updates.copy()
                self._pending_updates.clear()

                for entity_id, changes in updates.items():
                    if not changes:
                        continue

                    changes["entity_id"] = entity_id

                    try:
                        await self.hass.services.async_call(
                            DOMAIN, "set_state", changes
                        )
                    except Exception as e:
                        _LOGGER.error(
                            "Error updating entity %s: %s", entity_id, str(e))
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error(
                    "Unexpected error in update processor: %s", str(err))
                await asyncio.sleep(1)

    async def cleanup(self) -> None:
        """Clean up resources when the integration is unloaded."""
        for menu_state in self._menu_states.values():
            if menu_state.menu_timeout and not menu_state.menu_timeout.done():
                menu_state.menu_timeout.cancel()

            for timeout in menu_state.item_timeouts.values():
                if not timeout.done():
                    timeout.cancel()

        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
