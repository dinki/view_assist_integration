"""Loads modules for a View Assist device.

This code should not be edited unless you know what you are doing.
To add a new module, create a new file in the devices folder and add the module class
to the ALL_DEVICE_MODULES or VIEW_DEVICE_MODULES list.
"""

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from ..const import DEVICES, DOMAIN, PLATFORMS  # noqa: TID252
from ..helpers import get_master_config_entry  # noqa: TID252
from ..typed import (  # noqa: TID252
    DISPLAY_DEVICE_TYPES,
    VAConfigEntry,
    VAEvent,
    VAEventType,
)
from .background import BackgroundImageManager
from .display import DisplayManager
from .entity_listeners import EntityListeners
from .intents import DeviceIntentsHandler
from .menu import MenuManager
from .navigation import NavigationManager
from .status import StatusManager

_LOGGER = logging.getLogger(__name__)

DEVICE_MANAGER = "device_manager"

ALL_DEVICE_MODULES = [
    StatusManager,
    EntityListeners,
    NavigationManager,  # Added here to prevent error when navigation is called from non-display devices (e.g. mic) without error see issue #414 on View-Assist repo
    DeviceIntentsHandler,
]

VIEW_DEVICE_MODULES = [
    MenuManager,
    BackgroundImageManager,
    DisplayManager,
]


class DeviceManager:
    """Class to manage display related functionality."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the device manager."""
        self.hass = hass
        self.config = config
        self.name = config.runtime_data.core.name

    async def _async_wait_for_core_startup(self) -> bool:
        """Wait for core to finish starting up."""
        master_entry = get_master_config_entry(self.hass)
        iteration = 1
        try:
            async with asyncio.timeout(10):
                while master_entry.state != ConfigEntryState.LOADED:
                    # Log message every 2s
                    if iteration % 10 == 0:  # Log message every 2 seconds (0.2 * 10)
                        _LOGGER.debug(
                            "Waiting for master config to be available for %s",
                            self.name,
                        )
                    iteration += 1
                    await asyncio.sleep(0.2)
        except TimeoutError:
            _LOGGER.error(
                "Timeout waiting for master config to be available for %s",
                self.name,
            )
            return False
        return True

    async def async_setup(self) -> bool:
        """Set up the modules for a device."""
        is_first_entry = False

        result = await self._async_wait_for_core_startup()

        if not result:
            _LOGGER.error("Core startup failed for %s", self.name)
            return False

        _LOGGER.debug("Loading %s", self.name)

        # Add the device to the hass data structure
        if DEVICES not in self.hass.data[DOMAIN]:
            self.hass.data[DOMAIN][DEVICES] = {}
            is_first_entry = True

        if self.config.entry_id not in self.hass.data[DOMAIN][DEVICES]:
            self.hass.data[DOMAIN][DEVICES][self.config.entry_id] = {}

        # Request platform setups
        await self.hass.config_entries.async_forward_entry_setups(
            self.config, PLATFORMS
        )

        # Define modules to load
        modules = []

        if self.config.runtime_data.core.type in DISPLAY_DEVICE_TYPES:
            modules += VIEW_DEVICE_MODULES

        modules += ALL_DEVICE_MODULES

        # Load modules
        loading_tasks = set()
        for module in modules:
            loading_tasks.add(
                asyncio.create_task(self._async_load_module(module, is_first_entry))
            )

        setup_result = all(await asyncio.gather(*loading_tasks))

        if not setup_result:
            _LOGGER.error("Error setting up %s", self.name)
            return False

        # Notify that browser is registered
        browser_id = self.config.runtime_data.core.display_device
        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{browser_id}_event",
            VAEvent(VAEventType.BROWSER_REGISTERED),
        )

        _LOGGER.debug("Finished loading %s", self.name)
        return setup_result

    async def _async_load_module(
        self, module: Any, is_first_entry: bool = False
    ) -> bool:
        """Load a module asynchronously."""
        instance = module(self.hass, self.config)

        if hasattr(module, "wait_for_dependencies_and_setup"):
            await instance.wait_for_dependencies_and_setup()
        elif hasattr(module, "async_setup"):
            await instance.async_setup()

        if hasattr(module, "async_setup_once") and is_first_entry:
            await instance.async_setup_once()

        self.hass.data[DOMAIN][DEVICES][self.config.entry_id][module.__name__] = (
            instance
        )
        return True

    @staticmethod
    async def async_unload(hass: HomeAssistant, config: VAConfigEntry) -> bool:
        """Stop the DisplayManager."""
        name = config.runtime_data.core.name
        _LOGGER.debug("Unloading %s", name)

        is_last_entry = len(hass.data[DOMAIN][DEVICES]) == 1

        # Unload platforms
        await hass.config_entries.async_unload_platforms(config, PLATFORMS)

        # Define modules to unload
        modules = ALL_DEVICE_MODULES.copy()

        if config.runtime_data.core.type in DISPLAY_DEVICE_TYPES:
            modules += VIEW_DEVICE_MODULES

        # Unload modules
        loading_tasks = set()
        for module in modules:
            loading_tasks.add(
                asyncio.create_task(
                    DeviceManager._async_unload_module(
                        hass, config, module, is_last_entry
                    )
                )
            )

        unload_result = all(await asyncio.gather(*loading_tasks))

        if not unload_result:
            _LOGGER.error("Error unloading device functions for %s", name)
            return False

        return unload_result

    @staticmethod
    async def _async_unload_module(
        hass: HomeAssistant,
        config: VAConfigEntry,
        module: Any,
        is_last_entry: bool = False,
    ) -> bool:
        """Unload a module asynchronously."""
        if instance := hass.data[DOMAIN][DEVICES][config.entry_id].get(module.__name__):
            _LOGGER.debug(
                "Unloading %s for %s", module.__name__, config.runtime_data.core.name
            )
            if hasattr(instance, "async_unload_last") and is_last_entry:
                await instance.async_unload_last()
            if hasattr(instance, "async_unload"):
                await instance.async_unload()

            with contextlib.suppress(KeyError):
                del hass.data[DOMAIN][DEVICES][config.entry_id][module.__name__]

            # Remove device entry if no modules left
            if len(hass.data[DOMAIN][DEVICES][config.entry_id]) == 0:
                with contextlib.suppress(KeyError):
                    del hass.data[DOMAIN][DEVICES][config.entry_id]
        return True
