"""Navigation manager."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.dispatcher import async_dispatcher_send, callback

from ..const import ATTR_DEVICE, DEVICES, DOMAIN  # noqa: TID252
from ..helpers import get_config_entry_by_entity_id  # noqa: TID252
from ..typed import (  # noqa: TID252
    DISPLAY_DEVICE_TYPES,
    VAConfigEntry,
    VAEvent,
    VAEventType,
)
from .base import DeviceModule

ATTR_PATH = "path"
ATTR_REVERT_TIMEOUT = "revert_timeout"

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


class NavigationManager(DeviceModule):
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
        super().__init__(hass, config)
        self.name = config.runtime_data.core.name

        self._cycle_view_mode: bool = False
        self._cycle_interval: int = 15
        self._cycle_views: list[str] = []
        self._cycle_current_view_index: int = 0
        self._cycle_view_next_transition_time: int = 0

    async def async_setup_once(self) -> bool:
        """Set up navigation manager services that should only be registered once."""
        NavigationManagerServices(self._hass).register()
        return True

    async def async_setup(self) -> bool:
        """Set up the NavigationManager."""
        return True

    async def async_unload(self) -> None:
        """Stop the NavigationManager."""
        return True

    async def async_unload_last(self):
        """Unload the last instance of NavigationManager."""
        NavigationManagerServices(self._hass).unregister()
        return True

    def browser_navigate(
        self,
        path: str,
        view_data: dict[str, Any] | None = None,
        timeout: int | None = None,
    ):
        """Navigate browser to defined view.

        Optionally revert to another view after timeout.
        """

        # If not a display device then return.  Allows navigation to be called from other devices (e.g. mic) without error
        if self._config.runtime_data.core.type not in DISPLAY_DEVICE_TYPES:
            return

        # Set view data if provided
        if view_data is not None:
            async_dispatcher_send(
                self._hass,
                f"{DOMAIN}_{self._config.entry_id}_event",
                VAEvent(VAEventType.INTENT_UPDATE, {"view_data": view_data}),
            )

        # Validate path
        if not path.startswith("/"):
            path = f"/{path}"

        _LOGGER.debug(
            "Navigating: %s to path %s, mode: %s",
            self._config.runtime_data.core.name,
            path,
            self._config.runtime_data.default.mode,
        )

        # Send navigation event to VA JS Helper
        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self._config.entry_id}_event",
            VAEvent(
                VAEventType.NAVIGATION,
                {
                    "path": path,
                    "timeout": timeout
                    or self._config.runtime_data.default.view_timeout,
                },
            ),
        )

    def navigate_to_view(self, view: str, view_data: dict[str, Any] | None = None):
        """Navigate browser to a specific view."""

        # Attempt to get view path from config
        if hasattr(self._config.runtime_data.dashboard, view):
            path = getattr(self._config.runtime_data.dashboard, view)
        else:
            dashboard = self._config.runtime_data.dashboard.dashboard
            path = f"/{dashboard.removeprefix('/').removesuffix('/')}/{view}"
        self.browser_navigate(path=path, view_data=view_data)

    def navigate_home(self):
        """Navigate browser to home view."""
        path = (
            self._config.runtime_data.runtime_config_overrides.home
            or self._config.runtime_data.dashboard.home
        )
        self.browser_navigate(
            path=path,
        )


class NavigationManagerServices:
    """Class to manage navigation related services."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        self._hass = hass

    def register(self):
        """Register services."""
        self._hass.services.async_register(
            DOMAIN,
            "navigate",
            self._handle_navigate,
        )

    def unregister(self):
        """Unregister services."""
        self._hass.services.async_remove(DOMAIN, "navigate")

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
        entry = get_config_entry_by_entity_id(self._hass, entity_id)
        if entry:
            return NavigationManager.get(self._hass, entry)
        return None
