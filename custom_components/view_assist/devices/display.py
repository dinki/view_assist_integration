"""Mode management for the view_assist custom component."""

import asyncio
from asyncio import Task
import contextlib
from datetime import timedelta
from enum import StrEnum
import logging

from homeassistant.components.assist_satellite.entity import AssistSatelliteState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import DEVICES, DOMAIN, VAMode  # noqa: TID252
from ..typed import VAConfigEntry  # noqa: TID252
from .base import DeviceModule
from .navigation import NavigationManager
from .status import StatusManager


class TimeOutMode(StrEnum):
    """Enumeration for different timeout modes."""

    NORMAL = "normal"
    CYCLE = "cycle"


_LOGGER = logging.getLogger(__name__)


class DisplayManager(DeviceModule):
    """Class to manage display device modes."""

    _dependencies = ["NavigationManager", "MenuManager", "StatusManager"]

    @classmethod
    def get(cls, hass: HomeAssistant, config: VAConfigEntry) -> DisplayManager | None:
        """Get the instance for a config entry."""
        try:
            return hass.data[DOMAIN][DEVICES][config.entry_id][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the display manager."""
        super().__init__(hass, config)
        self._mode_history: list[str] = [VAMode.NORMAL]
        self._activity_monitoring_task: Task | None = None

        self._cycle_interval: int = 15
        self._cycle_current_view_index: int = 0

    async def async_setup(self) -> bool:
        """Set up the DisplayManager."""

        # Start inactivity monitoring for the display device
        self._activity_monitoring_task = self._hass.async_create_background_task(
            self.async_start_inactivity_monitoring(),
            name=f"start_inactivity_monitoring_{self._config.entry_id}",
        )
        return True

    async def async_unload(self) -> None:
        """Unload the DisplayManager."""
        if self._activity_monitoring_task:
            self._activity_monitoring_task.cancel()
            self._activity_monitoring_task = None

    async def async_start_inactivity_monitoring(self) -> None:
        """Start monitoring inactivity for the display device."""
        sm = StatusManager.get(self._hass, self._config)
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await asyncio.sleep(1)  # Check inactivity every 1 second

                # Skip if assist is active, on hold screen, or timer is sounding
                if (
                    sm.assist_state != AssistSatelliteState.IDLE
                    or (sm.hold and sm.current_path == sm.hold_view)
                    or sm.alarm_sounding
                ):
                    continue

                timeout = (
                    sm.config.default.view_timeout
                    if sm.mode != VAMode.CYCLE
                    else self._cycle_interval
                )

                if sm.force_activity_timeout_flag or (
                    sm.last_activity
                    and dt_util.now() - sm.last_activity > timedelta(seconds=timeout)
                ):
                    sm.force_activity_timeout_flag = False
                    view = self._choose_inactivity_view(sm)

                    if view and sm.current_path != view:
                        if nm := NavigationManager.get(self._hass, self._config):
                            nm.browser_navigate(view)

    def _choose_inactivity_view(self, sm: StatusManager) -> str | None:
        """Handle navigation when inactivity timeout occurs."""

        if sm.hold:
            return sm.hold_view if sm.current_path != sm.hold_view else None

        if sm.mode == VAMode.CYCLE:
            return self._get_cycle_next_view()

        if sm.is_music_playing:
            return (
                sm.config.dashboard.music
                if sm.current_path != sm.config.dashboard.music
                else None
            )

        return sm.config.dashboard.home

    def _get_cycle_next_view(self) -> str | None:
        if sm := StatusManager.get(self._hass, self._config):
            cycle_views = sm.config.dashboard.display_settings.cycle_views
            if cycle_views:
                self._cycle_current_view_index = (
                    self._cycle_current_view_index + 1
                ) % len(cycle_views)
                return self._get_view_path(cycle_views[self._cycle_current_view_index])
        return None

    def _get_view_path(self, view: str) -> str | None:
        if sm := StatusManager.get(self._hass, self._config):
            if hasattr(sm.config.dashboard, view):
                return getattr(sm.config.dashboard, view)
            return f"{sm.config.dashboard.dashboard}/{view}"
        return None
