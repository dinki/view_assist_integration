"""Storage manager for timers."""

from collections.abc import Callable  # noqa: I001
import contextlib
import inspect
import logging
import time
from typing import Any
from .typed import Duration

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ...const import DOMAIN  # noqa: TID252
from ...typed import VAEvent, VAEventType  # noqa: TID252
from .typed import (
    SnoozeInfo,
    TIMERS_STORE_NAME,
    Timer,
    TimerRemainingInfo,
    TimerInfo,
    TimerStatus,
)

_LOGGER = logging.getLogger(__name__)


class VATimerStore:
    """Class to manager timer store."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        self.hass = hass
        self.store = Store(hass, 1, TIMERS_STORE_NAME)
        self.listeners: dict[str, Callable] = {}
        self.timers: dict[str, Timer] = {}
        self.dirty = False

    async def save(self):
        """Save store."""
        if self.dirty:
            await self.store.async_save(self.timers)
            self.dirty = False

    async def load(self):
        """Load tiers from store."""
        stored: dict[str, Any] = await self.store.async_load()
        dirty = self.dirty
        if stored:
            for timer_id, timer in stored.items():
                try:
                    timer = Timer(**timer)
                    timer.timer_info = (
                        TimerInfo(**timer.timer_info)
                        if timer.timer_info
                        else TimerInfo()
                    )
                    timer.remaining_info = (
                        TimerRemainingInfo(**timer.remaining_info)
                        if timer.remaining_info
                        else TimerRemainingInfo()
                    )
                    timer.remaining_info.duration = (
                        Duration(**timer.remaining_info.duration)
                        if timer.remaining_info.duration
                        else Duration()
                    )
                    timer.snooze_info = (
                        SnoozeInfo(**timer.snooze_info)
                        if timer.snooze_info
                        else SnoozeInfo()
                    )
                    self.timers[timer_id] = timer
                except Exception as e:  # noqa: BLE001
                    _LOGGER.error("Failed to load timer %s: %s", timer_id, e)
                    await self.cancel_timer(timer_id)
        self.dirty = dirty
        if self.dirty:
            await self.save()

    async def updated(self, timer_id: str):
        """Store has been updated."""
        self.dirty = True
        if timer_id in self.timers:
            self.timers[timer_id].updated_at = time.mktime(dt_util.now().timetuple())

        for callback in self.listeners.values():
            if inspect.iscoroutinefunction(callback):
                await callback(self.timers)
            else:
                callback(self.timers)
        await self.save()

    def add_listener(self, entity, callback):
        """Add store updated listener."""
        self.listeners[entity] = callback

        def remove_listener():
            with contextlib.suppress(Exception):
                del self.listeners[entity]

        return remove_listener

    async def update_status(self, timer_id: str, status: TimerStatus):
        """Update timer current status."""
        self.timers[timer_id].status = status
        await self.updated(timer_id)

    async def cancel_timer(self, timer_id: str) -> bool:
        """Cancel timer."""
        if timer_id in self.timers:
            self.timers.pop(timer_id)
            await self.updated(timer_id)
            return True
        return False
