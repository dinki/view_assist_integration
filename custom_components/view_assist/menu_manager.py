"""Menu manager for View Assist."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_DISPLAY_SETTINGS,
    CONF_MENU_CONFIG,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    CONF_STATUS_ICONS,
    DEFAULT_VALUES,
    DOMAIN,
    VAMode,
)
from .helpers import (
    get_config_entry_by_entity_id,
    get_master_config_entry,
    get_sensor_entity_from_instance,
    normalize_status_items,
)
from .typed import VAConfigEntry, VAEvent, VAMenuConfig

_LOGGER = logging.getLogger(__name__)

StatusItemType = str | list[str]

SYSTEM_ICONS = ["mic", "mediaplayer", "dnd", "hold", "cycle"]


@dataclass
class MenuState:
    """Structured representation of a menu's state."""

    entity_id: str
    active: bool = False
    configured_items: list[str] = field(default_factory=list)
    launch_icons: list[str] = field(default_factory=list)
    system_icons: list[str] = field(default_factory=list)
    menu_timeout: asyncio.Task | None = None
    item_timeouts: dict[tuple[str, str, bool], asyncio.Task] = field(
        default_factory=dict
    )


class MenuManager:
    """Class to manage View Assist menus."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize menu manager."""
        self.hass = hass
        self.config = config
        self._menu_states: dict[str, MenuState] = {}
        self._pending_updates: dict[str, dict[str, Any]] = {}
        self._update_event = asyncio.Event()
        self._update_task: asyncio.Task | None = None
        self._initialized = False

        config.async_on_unload(self.cleanup)
        self.hass.bus.async_listen_once(
            "homeassistant_started", self._initialize_on_startup
        )

    def _ensure_menu_button_at_end(self, status_icons: list[str]) -> None:
        """Ensure menu button is always the rightmost (last) status icon."""
        if "menu" in status_icons:
            status_icons.remove("menu")
            status_icons.append("menu")

    def _arrange_status_icons(
        self,
        menu_items: list[str],
        system_icons: list[str], 
        launch_icons: list[str],
        show_menu_button: bool = False
    ) -> list[str]:
        """Arrange status icons in the correct order: menu → system → launch → menu button."""
        result = []

        for item in menu_items:
            if item != "menu" and item not in result:
                result.append(item)

        for icon in system_icons:
            if icon != "menu" and icon not in result:
                result.append(icon)

        for icon in launch_icons:
            if icon != "menu" and icon not in result:
                result.append(icon)

        if show_menu_button:
            self._ensure_menu_button_at_end(result)

        return result

    def _update_status_icons(
        self,
        current_icons: list[str],
        add_icons: list[str] = None,
        remove_icons: list[str] = None,
        menu_items: list[str] = None,
        system_icons: list[str] = None,
        launch_icons: list[str] = None,
        show_menu_button: bool = False,
    ) -> list[str]:
        """Update status icons maintaining proper ordering."""
        if menu_items is not None:
            return self._arrange_status_icons(
                menu_items, 
                system_icons or [], 
                launch_icons or [], 
                show_menu_button
            )

        result = current_icons.copy()

        if remove_icons:
            for icon in remove_icons:
                if icon == "menu" and show_menu_button:
                    continue
                if icon in result:
                    result.remove(icon)

        if add_icons:
            for icon in add_icons:
                if icon not in result and icon != "menu":
                    result.append(icon)

        if show_menu_button:
            self._ensure_menu_button_at_end(result)

        return result

    def _separate_icon_types(self, all_icons: list[str], menu_items: list[str]) -> tuple[list[str], list[str]]:
        """Separate icons into system icons and launch icons."""
        system_icons = []
        launch_icons = []

        for icon in all_icons:
            if icon == "menu":
                continue
            elif icon in SYSTEM_ICONS:
                system_icons.append(icon)
            elif icon not in menu_items:
                launch_icons.append(icon)

        return system_icons, launch_icons

    async def _initialize_on_startup(self, _event: Event) -> None:
        """Initialize when Home Assistant has fully started."""
        if self._initialized:
            return

        self._update_task = self.config.async_create_background_task(
            self.hass, self._update_processor(), name="VA Menu Manager"
        )

        # Initialize existing entities
        for entry_id in [
            e.entry_id for e in self.hass.config_entries.async_entries(DOMAIN)
        ]:
            entity_id = get_sensor_entity_from_instance(self.hass, entry_id)
            if entity_id:
                self._get_or_create_state(entity_id)

        self._initialized = True

    def _get_or_create_state(self, entity_id: str) -> MenuState:
        """Get or create a MenuState for the entity."""
        if entity_id not in self._menu_states:
            self._menu_states[entity_id] = MenuState(entity_id=entity_id)
            state = self.hass.states.get(entity_id)

            if state:
                menu_items = self._get_config_value(
                    entity_id, f"{CONF_DISPLAY_SETTINGS}.{CONF_MENU_ITEMS}", []
                )
                self._menu_states[entity_id].configured_items = menu_items or []

                status_icons = state.attributes.get(CONF_STATUS_ICONS, [])
                if status_icons:
                    system_icons, launch_icons = self._separate_icon_types(
                        status_icons, menu_items or []
                    )
                    self._menu_states[entity_id].system_icons = system_icons
                    self._menu_states[entity_id].launch_icons = launch_icons

                self._menu_states[entity_id].active = state.attributes.get(
                    "menu_active", False
                )

        return self._menu_states[entity_id]

    def _get_config_value(self, entity_id: str, key: str, default: Any = None) -> Any:
        """Get configuration value with hierarchy: entity > master > default."""
        # Check entity config
        entity_config = get_config_entry_by_entity_id(self.hass, entity_id)

        # Check entity config first
        if entity_config:
            # Check direct key
            if key in entity_config.options:
                return entity_config.options[key]

            # Check nested key
            if "." in key:
                section, setting = key.split(".")
                if (
                    section in entity_config.options
                    and isinstance(entity_config.options[section], dict)
                    and setting in entity_config.options[section]
                ):
                    return entity_config.options[section][setting]

        # Check master config
        master_config = get_master_config_entry(self.hass)
        if master_config:
            # Check direct key
            if key in master_config.options:
                return master_config.options[key]

            # Check nested key
            if "." in key:
                section, setting = key.split(".")
                if (
                    section in master_config.options
                    and isinstance(master_config.options[section], dict)
                    and setting in master_config.options[section]
                ):
                    return master_config.options[section][setting]

        # Check defaults
        if key in DEFAULT_VALUES:
            return DEFAULT_VALUES[key]

        if "." in key:
            section, setting = key.split(".")
            if (
                section in DEFAULT_VALUES
                and isinstance(DEFAULT_VALUES[section], dict)
                and setting in DEFAULT_VALUES[section]
            ):
                return DEFAULT_VALUES[section][setting]

        return default

    async def toggle_menu(
        self, entity_id: str, show: bool | None = None, timeout: int | None = None
    ) -> None:
        """Toggle menu visibility for an entity."""
        await self._ensure_initialized()

        # Validate entity and config
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            _LOGGER.error("Config entry not found for %s", entity_id)
            return

        # Get menu configuration
        menu_config = self._get_config_value(
            entity_id,
            f"{CONF_DISPLAY_SETTINGS}.{CONF_MENU_CONFIG}",
            VAMenuConfig.DISABLED,
        )

        # Check if menu is enabled
        if menu_config == VAMenuConfig.DISABLED:
            _LOGGER.warning("Menu is not enabled for %s", entity_id)
            return

        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.warning("Entity %s not found", entity_id)
            return

        # Get menu state and settings
        menu_state = self._get_or_create_state(entity_id)
        current_active = menu_state.active
        show = show if show is not None else not current_active

        self._cancel_timeout(entity_id)

        # Check if menu button should be shown
        show_menu_button = menu_config == VAMenuConfig.ENABLED_VISIBLE

        # Always show all icons (system + launch + menu button) in status_icons
        # The frontend JavaScript handles which ones to display based on menu_active state
        updated_icons = self._arrange_status_icons(
            [],
            menu_state.system_icons,
            menu_state.launch_icons,
            show_menu_button
        )

        # Update menu state
        menu_state.active = show
        changes = {"status_icons": updated_icons, "menu_active": show}

        # Handle timeout for auto-close
        if show:
            if timeout is not None:
                self._setup_timeout(entity_id, timeout)
            else:
                menu_timeout = self._get_config_value(
                    entity_id, f"{CONF_DISPLAY_SETTINGS}.{CONF_MENU_TIMEOUT}", 0
                )
                if menu_timeout > 0:
                    self._setup_timeout(entity_id, menu_timeout)

        # Apply changes
        if changes:
            await self._update_entity_state(entity_id, changes)

            # Notify via dispatcher
            if config_entry:
                async_dispatcher_send(
                    self.hass,
                    f"{DOMAIN}_{config_entry.entry_id}_event",
                    VAEvent("menu_update", {"menu_active": show}),
                )

    async def update_system_icons(
        self, entity_id: str, add_icons: list[str] = None, remove_icons: list[str] = None
    ) -> None:
        """Update system icons (called by entity listeners)."""
        await self._ensure_initialized()
        menu_state = self._get_or_create_state(entity_id)

        # Get menu configuration
        menu_config = self._get_config_value(
            entity_id,
            f"{CONF_DISPLAY_SETTINGS}.{CONF_MENU_CONFIG}",
            VAMenuConfig.DISABLED,
        )
        show_menu_button = menu_config == VAMenuConfig.ENABLED_VISIBLE

        # Update system icons list
        if remove_icons:
            for icon in remove_icons:
                if icon in menu_state.system_icons:
                    menu_state.system_icons.remove(icon)

        if add_icons:
            for icon in add_icons:
                if icon in SYSTEM_ICONS and icon not in menu_state.system_icons:
                    menu_state.system_icons.append(icon)

        # Always rebuild status icons with all icons (frontend handles display)
        updated_icons = self._arrange_status_icons(
            [],
            menu_state.system_icons,
            menu_state.launch_icons,
            show_menu_button
        )

        # Apply changes
        changes = {"status_icons": updated_icons}
        await self._update_entity_state(entity_id, changes)

    async def add_status_item(
        self,
        entity_id: str,
        status_item: StatusItemType,
        menu: bool = False,
        timeout: int | None = None,
    ) -> None:
        """Add status item(s) to the entity's status icons or menu items."""
        # Normalize input and validate
        items = normalize_status_items(status_item)
        if isinstance(items, str):
            items = [items]
        elif not items:
            _LOGGER.warning("No valid items to add")
            return

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            _LOGGER.warning("No config entry found for entity %s", entity_id)
            return

        await self._ensure_initialized()
        menu_state = self._get_or_create_state(entity_id)

        # Get menu configuration
        menu_config = self._get_config_value(
            entity_id,
            f"{CONF_DISPLAY_SETTINGS}.{CONF_MENU_CONFIG}",
            VAMenuConfig.DISABLED,
        )
        show_menu_button = menu_config == VAMenuConfig.ENABLED_VISIBLE

        changes = {}
        if menu:
            # Add to menu items (runtime only - don't persist via service calls)
            updated_items = menu_state.configured_items.copy()
            changed = False

            for item in items:
                if item not in updated_items:
                    updated_items.append(item)
                    changed = True

            if changed:
                menu_state.configured_items = updated_items
                changes["menu_items"] = updated_items

                # Always update status_icons with all icons (frontend handles display)
                updated_icons = self._arrange_status_icons(
                    [],
                    menu_state.system_icons,
                    menu_state.launch_icons,
                    show_menu_button
                )
                changes["status_icons"] = updated_icons
        else:
            # Add to launch icons
            changed = False
            for item in items:
                if item not in menu_state.launch_icons:
                    menu_state.launch_icons.append(item)
                    changed = True

            if changed:
                # Always rebuild status icons with all icons (frontend handles display)
                updated_icons = self._arrange_status_icons(
                    [],
                    menu_state.system_icons,
                    menu_state.launch_icons,
                    show_menu_button
                )
                changes["status_icons"] = updated_icons

        # Apply changes
        if changes:
            await self._update_entity_state(entity_id, changes)

        # Set up timeouts if needed
        if timeout is not None:
            for item in items:
                await self._setup_item_timeout(entity_id, item, timeout, menu)

    async def remove_status_item(
        self, entity_id: str, status_item: StatusItemType, from_menu: bool = False
    ) -> None:
        """Remove status item(s) from the entity's status icons or menu items."""
        # Normalize input and validate
        items = normalize_status_items(status_item)
        if isinstance(items, str):
            items = [items]
        elif not items:
            _LOGGER.warning("No valid items to remove")
            return

        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if not config_entry:
            return

        await self._ensure_initialized()
        menu_state = self._get_or_create_state(entity_id)

        # Get menu configuration
        menu_config = self._get_config_value(
            entity_id,
            f"{CONF_DISPLAY_SETTINGS}.{CONF_MENU_CONFIG}",
            VAMenuConfig.DISABLED,
        )
        show_menu_button = menu_config == VAMenuConfig.ENABLED_VISIBLE

        changes = {}
        if from_menu:
            # Remove from menu items (runtime only - don't persist via service calls)
            updated_items = [
                item for item in menu_state.configured_items if item not in items
            ]

            if updated_items != menu_state.configured_items:
                menu_state.configured_items = updated_items
                changes["menu_items"] = updated_items

                # Always update status_icons with all icons (frontend handles display)
                updated_icons = self._arrange_status_icons(
                    [],  # Menu items handled by frontend
                    menu_state.system_icons,
                    menu_state.launch_icons,
                    show_menu_button
                )
                changes["status_icons"] = updated_icons
        else:
            # Remove from launch icons
            changed = False
            for item in items:
                if item in menu_state.launch_icons:
                    menu_state.launch_icons.remove(item)
                    changed = True

            if changed:
                # Always rebuild status icons with all icons (frontend handles display)
                updated_icons = self._arrange_status_icons(
                    [],  # Menu items handled by frontend
                    menu_state.system_icons,
                    menu_state.launch_icons,
                    show_menu_button
                )
                changes["status_icons"] = updated_icons

        # Apply changes and cancel timeouts
        if changes:
            await self._update_entity_state(entity_id, changes)

        for item in items:
            self._cancel_item_timeout(entity_id, item, from_menu)

    def _setup_timeout(self, entity_id: str, timeout: int) -> None:
        """Setup timeout for menu."""
        menu_state = self._get_or_create_state(entity_id)
        self._cancel_timeout(entity_id)

        async def _timeout_task() -> None:
            try:
                await asyncio.sleep(timeout)
                await self.toggle_menu(entity_id, False)
            except asyncio.CancelledError:
                pass

        menu_state.menu_timeout = self.config.async_create_background_task(
            self.hass, _timeout_task(), name=f"VA Menu Timeout {entity_id}"
        )

    def _cancel_timeout(self, entity_id: str) -> None:
        """Cancel any existing timeout for an entity."""
        if entity_id in self._menu_states and self._menu_states[entity_id].menu_timeout:
            menu_timeout = self._menu_states[entity_id].menu_timeout
            if not menu_timeout.done():
                menu_timeout.cancel()
                self._menu_states[entity_id].menu_timeout = None

    async def _setup_item_timeout(
        self, entity_id: str, menu_item: str, timeout: int, is_menu_item: bool = False
    ) -> None:
        """Set up a timeout for a specific menu item."""
        menu_state = self._get_or_create_state(entity_id)
        item_key = (entity_id, menu_item, is_menu_item)
        self._cancel_item_timeout(entity_id, menu_item, is_menu_item)

        async def _item_timeout_task() -> None:
            try:
                await asyncio.sleep(timeout)
                await self.remove_status_item(entity_id, menu_item, is_menu_item)
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
        self, entity_id: str, changes: dict[str, Any]
    ) -> None:
        """Queue entity state update."""
        if not changes:
            return

        if entity_id not in self._pending_updates:
            self._pending_updates[entity_id] = {}

        self._pending_updates[entity_id].update(changes)
        self._update_event.set()

    async def _update_processor(self) -> None:
        """Process updates as they arrive."""
        try:
            while True:
                await self._update_event.wait()
                self._update_event.clear()

                updates = self._pending_updates.copy()
                self._pending_updates.clear()

                for entity_id, changes in updates.items():
                    if changes:
                        changes["entity_id"] = entity_id
                        try:
                            await self.hass.services.async_call(
                                DOMAIN, "set_state", changes
                            )
                        except Exception as err:  # noqa: BLE001
                            _LOGGER.error("Error updating %s: %s", entity_id, str(err))

        except asyncio.CancelledError:
            pass

    async def _ensure_initialized(self) -> None:
        """Ensure the menu manager is initialized."""
        if not self._initialized:
            self._update_task = self.config.async_create_background_task(
                self.hass, self._update_processor(), name="VA Menu Manager"
            )

            for entry_id in [
                e.entry_id for e in self.hass.config_entries.async_entries(DOMAIN)
            ]:
                entity_id = get_sensor_entity_from_instance(self.hass, entry_id)
                if entity_id:
                    self._get_or_create_state(entity_id)

            self._initialized = True

    async def cleanup(self) -> None:
        """Clean up resources."""
        for menu_state in self._menu_states.values():
            if menu_state.menu_timeout and not menu_state.menu_timeout.done():
                menu_state.menu_timeout.cancel()

            for timeout in menu_state.item_timeouts.values():
                if not timeout.done():
                    timeout.cancel()

        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
