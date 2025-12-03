"""Navigation manager."""

from __future__ import annotations

import asyncio
from asyncio import Task
import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.dispatcher import async_dispatcher_send, callback

from ..const import ATTR_DEVICE, DEVICES, DOMAIN, VAMode  # noqa: TID252
from ..helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_revert_settings_for_mode,
)
from ..typed import VAConfigEntry, VAEvent, VAEventType  # noqa: TID252

ATTR_PATH = "path"
ATTR_REVERT_TIMEOUT = "revert_timeout"
NAVIGATION_MANAGER = "navigation_manager"

NAVIGATE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Required(ATTR_PATH): str,
        vol.Optional(ATTR_REVERT_TIMEOUT, default=20): cv.positive_int,
    }
)

_LOGGER = logging.getLogger(__name__)


class NavigationManager:
    """Class to manage navigation within the dashboard."""

    @classmethod
    def get(
        cls, hass: HomeAssistant, config: VAConfigEntry
    ) -> NavigationManager | None:
        """Get the instance for a config entry."""
        try:
            return hass.data[DOMAIN][DEVICES][config.entry_id][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the navigation manager."""
        self.hass = hass
        self.config = config
        self.name = config.runtime_data.core.name

        self.revert_view_task: Task | None = None
        self.cycle_view_task: Task | None = None
        self.revert_timeout = config.runtime_data.default.view_timeout

    async def async_setup_once(self) -> bool:
        """Set up navigation manager services that should only be registered once."""
        NavigationManagerServices(self.hass).register()
        return True

    async def async_setup(self) -> bool:
        """Set up the NavigationManager."""
        return True

    async def async_unload(self) -> None:
        """Stop the NavigationManager."""
        return True

    async def async_unload_last(self):
        """Unload the last instance of NavigationManager."""
        NavigationManagerServices(self.hass).unregister()
        return True

    def browser_navigate(
        self,
        path: str,
        timeout: int | None = None,
        is_revert_action: bool = False,
    ):
        """Navigate browser to defined view.

        Optionally revert to another view after timeout.
        """

        # If new navigate before revert timer has expired, cancel revert timer.
        if not is_revert_action:
            self.cancel_display_revert_task()

        # Validate path
        if not path.startswith("/"):
            path = f"/{path}"

        _LOGGER.debug(
            "Navigating: %s to path %s with timeout of %s seconds, mode: %s",
            self.config.runtime_data.core.name,
            path,
            timeout,
            self.config.runtime_data.default.mode,
        )

        # Update current_path attribute
        self.config.runtime_data.extra_data["current_path"] = path
        
        # Notify sensor entity to update (triggers schedule_update_ha_state)
        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{self.config.entry_id}_event",
            VAEvent(VAEventType.CONFIG_UPDATE),
        )

        # Send navigation event to VA JS Helper
        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{self.config.entry_id}_event",
            VAEvent(VAEventType.NAVIGATION, {"path": path}),
        )

        # If this was a revert action, end here
        if is_revert_action:
            return

        # If timeout set to 0, do not revert
        if timeout == 0:
            return

        # Find required revert action
        revert, revert_view = get_revert_settings_for_mode(
            self.config.runtime_data.default.mode
        )
        if (
            revert_view == "home"
            and self.config.runtime_data.runtime_config_overrides.home
        ):
            revert_path = self.config.runtime_data.runtime_config_overrides.home
        else:
            revert_path = (
                getattr(self.config.runtime_data.dashboard, revert_view)
                if revert_view
                else None
            )

        # Set revert action if required
        if revert and path != revert_path:
            timeout = (
                self.config.runtime_data.default.view_timeout
                if timeout is None
                else timeout
            )
            _LOGGER.debug("Adding revert to %s in %ss", revert_path, timeout)
            self.revert_view_task = self.hass.async_create_task(
                self._display_revert_delay_task(path=revert_path, timeout=timeout)
            )

    def navigate_home(self):
        """Navigate browser to home view."""
        path = (
            self.config.runtime_data.runtime_config_overrides.home
            if self.config.runtime_data.runtime_config_overrides.home
            else self.config.runtime_data.dashboard.dashboard
        )
        self.browser_navigate(
            path=path,
            timeout=0,
            is_revert_action=False,
        )

    async def _display_revert_delay_task(self, path: str, timeout: int = 0):
        """Display revert function.  To be called from task."""
        if timeout:
            await asyncio.sleep(timeout)
            self.browser_navigate(path=path, is_revert_action=True)

    def cancel_display_revert_task(self):
        """Cancel any existing revert timer task."""
        if self.revert_view_task and not self.revert_view_task.done():
            _LOGGER.debug("Cancelled revert task")
            self.revert_view_task.cancel()
            self.revert_view_task = None

    def start_display_view_cycle(self, views: list[str]):
        """Start cycling display."""
        if self.cycle_view_task and not self.cycle_view_task.done():
            _LOGGER.debug("Cycle display already running")
            return
        self.cycle_view_task = self.hass.async_create_task(
            self._async_display_view_cycle_runner(views)
        )

    async def _async_display_view_cycle_runner(self, views: list[str]):
        """Cycle display."""
        view_index = 0
        _LOGGER.debug("Cycle display started")
        while self.config.runtime_data.default.mode == VAMode.CYCLE:
            view_index = view_index % len(views)
            _LOGGER.debug("Cycling to view: %s", views[view_index])
            self.browser_navigate(
                f"{self.config.runtime_data.dashboard.dashboard}/{views[view_index]}"
            )
            view_index += 1
            await asyncio.sleep(self.config.runtime_data.default.view_timeout)

    def stop_cycle_display(self):
        """Stop cycling display."""
        if self.cycle_view_task and not self.cycle_view_task.done():
            _LOGGER.debug("Stopping cycle display")
            self.cycle_view_task.cancel()
            self.cycle_view_task = None


class NavigationManagerServices:
    """Class to manage navigation related services."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        self.hass = hass

    def register(self):
        """Register services."""
        self.hass.services.async_register(
            DOMAIN,
            "navigate",
            self._handle_navigate,
        )

    def unregister(self):
        """Unregister services."""
        self.hass.services.async_remove(DOMAIN, "navigate")

    @callback
    def _handle_navigate(self, call: ServiceCall):
        """Handle a navigate to view call."""

        entity_id = call.data.get(ATTR_DEVICE)
        path = call.data.get(ATTR_PATH)
        timeout = call.data.get(ATTR_REVERT_TIMEOUT)

        # get config entry from entity id to allow access to browser_id parameter
        if navigation_manager := self._get_navigation_manager(entity_id):
            if path == "home":
                navigation_manager.navigate_home()
            else:
                navigation_manager.browser_navigate(path=path, timeout=timeout)
        else:
            _LOGGER.error("No navigation manager found for entity_id: %s", entity_id)

    def _get_navigation_manager(self, entity_id: str) -> NavigationManager | None:
        """Get the menu manager for an entity id."""
        entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if entry:
            return NavigationManager.get(self.hass, entry)
        return None
