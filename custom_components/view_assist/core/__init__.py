"""Loads core modules for View Assist.

This code should not be edited unless you know what you are doing.
To add a new module, create a new file in the core folder and add the module class
to the LOAD_MODULES list.
"""

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from ..assets import AssetsManager  # noqa: TID252
from ..const import DOMAIN  # noqa: TID252
from ..helpers import get_integration_entries  # noqa: TID252
from ..typed import VAConfigEntry  # noqa: TID252
from .alarm_repeater import AlarmRepeater
from .http import HTTPManager
from .intents import IntentsManager
from .javascript import JSModuleRegistration
from .services import Services
from .templates import TemplatesManager
from .timers import TimerManager
from .translator import Translator
from .websocket import WebsocketManager

_LOGGER = logging.getLogger(__name__)

LOAD_MODULES = [
    HTTPManager,
    JSModuleRegistration,
    TemplatesManager,
    AssetsManager,
    Translator,
    Services,
    TimerManager,
    AlarmRepeater,
    WebsocketManager,
    IntentsManager,
]


class CoreManager:
    """Class to manage core functions."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config

    async def async_start(self, *args) -> bool:
        """Set up the Core Functions."""
        _LOGGER.debug("Loading core functions")

        loader_tasks = set()
        for module in LOAD_MODULES:
            loader_tasks.add(asyncio.create_task(self._async_load_module(module)))

        setup_result = all(await asyncio.gather(*loader_tasks))

        # Load update platform
        if self.config.runtime_data.integration.enable_updates:
            _LOGGER.debug("Loading %s platform", Platform.UPDATE)
            await self.hass.config_entries.async_forward_entry_setups(
                self.config, [Platform.UPDATE]
            )

        # Reload any running device config entries to pick up core changes
        if entries := get_integration_entries(self.hass):
            for entry in entries:
                if entry.state == ConfigEntryState.LOADED:
                    _LOGGER.debug("Reloading config entry %s", entry.title)
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)

        return setup_result

    async def _async_load_module(self, module) -> bool:
        """Load a module."""
        instance = module(self.hass, self.config)
        _LOGGER.debug("Loading %s", module.__name__)
        if hasattr(instance, "async_setup"):
            result = await instance.async_setup()
            self.hass.data[DOMAIN][module.__name__] = instance
            return result
        return False

    @staticmethod
    async def async_unload(hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Stop the Core Functions."""
        _LOGGER.debug("Unloading core functions")

        # Unload update platform
        if config.runtime_data.integration.enable_updates:
            _LOGGER.debug("Unloading update notifications")
            await hass.config_entries.async_unload_platforms(config, [Platform.UPDATE])

        unloader_tasks = set()
        for module in LOAD_MODULES:
            if hasattr(module, "async_unload"):
                unloader_tasks.add(
                    asyncio.create_task(
                        CoreManager._async_unload_module(hass, config, module)
                    )
                )

        return all(await asyncio.gather(*unloader_tasks))

    @staticmethod
    async def _async_unload_module(
        hass: HomeAssistant, config: VAConfigEntry, module: Any
    ) -> None:
        """Unload a module."""
        _LOGGER.debug("Unloading %s", module.__name__)
        instance = hass.data[DOMAIN].get(module.__name__)
        if instance and hasattr(instance, "async_unload"):
            result = await instance.async_unload()
            hass.data[DOMAIN].pop(module.__name__, None)
            return result
        return False
