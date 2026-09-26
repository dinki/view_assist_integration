"""Device module base class."""

import asyncio
import logging
from typing import final

from homeassistant.core import HomeAssistant

from ..typed import VAConfigEntry  # noqa: TID252
from . import DEVICES, DOMAIN

_LOGGER = logging.getLogger(__name__)
DEPENDECY_LOAD_TIMEOUT = 20  # Timeout for loading dependencies in seconds


class DeviceModule:
    """Base class for all device modules."""

    _dependencies = []

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the device module."""
        self._hass = hass
        self._config = config
        self._initialised = False

    @final
    async def wait_for_dependencies_and_setup(self):
        """Set up the module and wait for its dependencies to be ready."""
        if self._dependencies:
            try:
                async with asyncio.timeout(
                    DEPENDECY_LOAD_TIMEOUT
                ):  # Wait for dependencies with a timeout of 20 seconds
                    # Here you would typically check if each dependency is ready
                    loaded = False
                    while not loaded:
                        for dependency in self._dependencies:
                            if not self._hass.data[DOMAIN][DEVICES][
                                self._config.entry_id
                            ].get(dependency):
                                await asyncio.sleep(
                                    0.1
                                )  # Wait for a short period before checking again
                                continue
                            loaded = True
            except TimeoutError as ex:
                raise TimeoutError(
                    f"Failed to load dependencies for {self.__class__.__name__} for device {self._config.entry_id} within the timeout period."
                ) from ex

        if hasattr(self, "async_setup"):
            _LOGGER.debug(
                "Loading %s for %s",
                self.__class__.__name__,
                self._config.runtime_data.core.name,
            )
            await self.async_setup()
        self._initialised = True
        return True

    async def async_setup(self):
        """Run on setup for the device."""
        return True

    async def async_setup_once(self):
        """Run once on setup for the first device only."""
        return True

    async def async_unload(self) -> bool:
        """Run on unload for the device."""
        return True

    async def async_unload_last(self) -> bool:
        """Run on unload when it is the last device."""
        return True
