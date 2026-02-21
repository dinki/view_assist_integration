"""Class to handle timers with persistent storage."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import datetime as dt
from enum import StrEnum
import inspect
import logging
import math
import time
from typing import Any
import zoneinfo

import voluptuous as vol

from homeassistant.components.intent import (
    TIMER_DATA,
    TimerEventType,
    TimerInfo as IntentTimerInfo,
    TimerManager as IntentTimerManager,
)
from homeassistant.components.intent.timers import _normalize_name
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_NAME, ATTR_TIME
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import ulid as ulid_util

from ..const import (  # noqa: TID252
    ATTR_EXTRA,
    ATTR_INCLUDE_EXPIRED,
    ATTR_LANGUAGE,
    ATTR_REMOVE_ALL,
    ATTR_TIMER_ID,
    ATTR_TYPE,
    DOMAIN,
)
from ..helpers import (  # noqa: TID252
    get_entity_id_from_conversation_device_id,
    get_mic_device_domain,
    get_mic_device_id_from_entity_id,
    get_mimic_entity_id,
)
from ..typed import VAEvent, VAEventType  # noqa: TID252
from .translator import Normaliser, TimerInfo, Translator

_LOGGER = logging.getLogger(__name__)


# Event name prefixes
VA_EVENT_PREFIX = "va_timer_{}"
VA_COMMAND_EVENT_PREFIX = "va_timer_command_{}"
TIMERS = "timers"
TIMERS_STORE_NAME = f"{DOMAIN}.{TIMERS}"


class TimerClass(StrEnum):
    """Timer class."""

    ALARM = "alarm"
    REMINDER = "reminder"
    TIMER = "timer"
    COMMAND = "command"


class TimerType(StrEnum):
    """Timer type."""

    TIME = "time"
    INTERVAL = "interval"


class TimerStatus(StrEnum):
    """Timer status."""

    INACTIVE = "inactive"
    RUNNING = "running"
    EXPIRED = "expired"
    SNOOZED = "snoozed"


class TimerEvent(StrEnum):
    """Event enums."""

    STARTED = "started"
    WARNING = "warning"
    EXPIRED = "expired"
    SNOOZED = "snoozed"
    CANCELLED = "cancelled"


@dataclass
class Timer:
    """Class to hold timer."""

    id: str
    timer_class: TimerClass
    timer_type: TimerType = field(default_factory=TimerType.INTERVAL)
    name: str | None = None
    expires_at: int = 0
    original_expires_at: int = 0
    pre_expire_warning: int = 0
    entity_id: str | None = None
    conversation_device_id: str | None = None
    status: TimerStatus = field(default_factory=TimerStatus.INACTIVE)
    created_at: int = 0
    created_at_monotonic: int = 0
    updated_at: int = 0
    extra_info: dict[str, Any] | None = None


def get_formatted_time(timer_dt: dt.datetime, h24format: bool = False) -> str:
    """Format datetime to time."""

    if h24format:
        if timer_dt.second:
            return timer_dt.strftime("%-H:%M:%S")
        return timer_dt.strftime("%-H:%M")

    if timer_dt.second:
        return timer_dt.strftime("%-I:%M:%S %p")
    return timer_dt.strftime("%-I:%M %p")


def get_named_day(timer_dt: dt.datetime, dt_now: dt.datetime) -> str:
    """Return a named day or date."""
    days_diff = timer_dt.day - dt_now.day
    if days_diff == 0:
        return "Today"
    if days_diff == 1:
        return "Tomorrow"
    if days_diff < 7:
        return timer_dt.strftime("%A")
    return timer_dt.strftime("%-d %B")


def encode_datetime_to_human(
    timer_type: str,
    timer_dt: dt.datetime,
    tz: zoneinfo.ZoneInfo,
    h24format: bool = False,
) -> str:
    """Encode datetime into human speech sentence."""

    def declension(term: str, qty: int) -> str:
        if qty > 1:
            return f"{term}s"
        return term

    dt_now = dt.datetime.now(tz=tz)
    delta = timer_dt - dt_now
    delta_s = math.ceil(delta.total_seconds())

    if timer_type == "interval":
        minutes, seconds = divmod(delta_s, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        response = []
        if days:
            response.append(f"{days} {declension('day', days)}")
        if hours:
            response.append(f"{hours} {declension('hour', hours)}")
        if minutes:
            response.append(f"{minutes} {declension('minute', minutes)}")
        if seconds:
            response.append(f"{seconds} {declension('second', seconds)}")

        # Now create sentence
        duration: str = ""
        for i, entry in enumerate(response):
            if i == len(response) - 1 and duration:
                duration += " and " + entry
            else:
                duration += " " + entry

        return duration.strip()

    if timer_type == "time":
        # do date bit - today, tomorrow, day of week if in next 7 days, date
        output_date = get_named_day(timer_dt, dt_now)
        output_time = get_formatted_time(timer_dt, h24format)
        return f"{output_date} at {output_time}"

    return timer_dt


def make_singular(sentence: str) -> str:
    """Make a time senstence singluar."""
    if sentence[-1:].lower() == "s":
        return sentence[:-1]
    return sentence


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
        if stored:
            # stored = await self.migrate(stored)
            for timer_id, timer in stored.items():
                self.timers[timer_id] = Timer(**timer)
        self.dirty = False

    async def migrate(self, stored: dict[str, Any]) -> dict[str, Any]:
        """Migrate stored data."""
        # Migrate to entity id from device id
        migrated = False
        for timer in stored.values():
            if timer.get("device_id"):
                migrated = True
                timer["entity_id"] = get_entity_id_from_conversation_device_id(
                    self.hass, timer["device_id"]
                )
                del timer["device_id"]

        if migrated:
            await self.save()
        return stored

    async def updated(self, timer_id: str):
        """Store has been updated."""
        self.dirty = True
        if timer_id in self.timers:
            self.timers[timer_id].updated_at = time.mktime(
                dt.datetime.now().timetuple()
            )

        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_event",
            VAEvent(VAEventType.TIMER_UPDATE),
        )

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


class TimerManager:
    """Class to handle VA timers."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> TimerManager | None:
        """Get the timer manager instance."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: ConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.tz: zoneinfo.ZoneInfo = zoneinfo.ZoneInfo(self.hass.config.time_zone)

        self.store = VATimerStore(hass)
        self.timer_tasks: dict[str, asyncio.Task] = {}

    async def async_setup(self) -> bool:
        """Set up the Timer Manager."""

        # Register services
        TimerManagerServices(self.hass).register()

        # Initialise timer store
        await self.store.load()

        # Load and start any existing timers from storage
        if self.store.timers:
            # Removed any in expired status on restart as event already got fired
            expired_timers = [
                timer_id
                for timer_id, timer in self.store.timers.items()
                if timer.status == TimerStatus.EXPIRED
            ]
            for timer_id in expired_timers:
                self.store.timers.pop(timer_id, None)

            for timer in self.store.timers.values():
                await self.start_timer(timer)

        return True

    async def async_unload(self) -> bool:
        """Unload Timer Manager."""

        # Cancel any timer tasks
        for task in self.timer_tasks.values():
            task.cancel()
        self.timer_tasks = {}

        # Unregister services
        TimerManagerServices(self.hass).unregister()

        return True

    async def add_timer(
        self,
        timer_class: TimerClass,
        device_id: str | None,
        entity_id: str | None,
        timer_info: TimerInfo | None,
        name: str | None = None,
        pre_expire_warning: int = 10,
        start: bool = True,
        extra_info: dict[str, Any] | None = None,
    ) -> tuple:
        """Add timer to store."""

        if not entity_id:
            if not (entity_id := self._get_entity_id(device_id)):
                raise vol.Invalid("Invalid device or entity id")

        # calculate expiry time from TimerInfo
        expiry = self.get_expiry_from_timerinfo(timer_info)

        _LOGGER.debug("Adding timer: %s, %s, %s", entity_id, timer_info, expiry)

        expires_unix_ts = round(expiry.timestamp()) if expiry else 0
        time_now_unix = round(dt.datetime.now(tz=self.tz).timestamp())

        if not (
            duplicate_timer := self.is_duplicate_timer(entity_id, name, expires_unix_ts)
        ):
            # Add timer_info to extra_info
            extra_info["timer_info"] = timer_info

            timer = Timer(
                id=ulid_util.ulid_now(),
                timer_class=timer_class.lower(),
                timer_type="time" if timer_info.is_time else "interval",
                original_expires_at=expires_unix_ts,
                expires_at=expires_unix_ts,
                name=name,
                entity_id=entity_id,
                conversation_device_id=device_id,
                pre_expire_warning=pre_expire_warning,
                created_at=time_now_unix,
                created_at_monotonic=time.monotonic_ns(),
                updated_at=time_now_unix,
                status=TimerStatus.INACTIVE,
                extra_info=extra_info,
            )

            self.store.timers[timer.id] = timer
            await self.store.save()

            if start:
                await self.start_timer(timer)

            # encoded_time = encode_datetime_to_human(timer.timer_type, expiry)
            return (
                "timer_named_set" if timer.name else "timer_set",
                self.format_timer_output(timer),
            )

        return "timer_already_exists", self.format_timer_output(duplicate_timer)

    async def start_timer(self, timer: Timer):
        """Start timer running."""

        total_seconds = round(
            timer.expires_at - dt.datetime.now(tz=self.tz).timestamp()
        )

        # Fire event if total seconds -ve
        # likely caused by timer expiring during restart
        if total_seconds < 1:
            await self._timer_finished(timer.id)
        else:
            if timer.pre_expire_warning and timer.pre_expire_warning >= total_seconds:
                # Create task to wait for timer duration with no warning
                self.timer_tasks[timer.id] = self.config.async_create_background_task(
                    self.hass,
                    self._wait_for_timer(
                        timer.id, total_seconds, timer.expires_at, fire_warning=False
                    ),
                    name=f"Timer {timer.id}",
                )
                _LOGGER.debug(
                    "Started %s timer for %ss, with no warning event",
                    timer.name,
                    total_seconds,
                )
            else:
                # Create task to wait for timer duration minus any pre_expire_warning time
                self.timer_tasks[timer.id] = self.config.async_create_background_task(
                    self.hass,
                    self._wait_for_timer(
                        timer.id,
                        total_seconds - timer.pre_expire_warning,
                        timer.expires_at,
                    ),
                    name=f"Timer {timer.id}",
                )
                _LOGGER.debug(
                    "Started %s timer for %ss, with warning event at %ss",
                    timer.name,
                    total_seconds,
                    total_seconds - timer.pre_expire_warning,
                )

            # Set timer status
            # if timer.status == TimerStatus.SNOOZED:
            #    return

            device_domain = get_mic_device_domain(self.hass, timer.entity_id)
            if device_domain == "esphome":
                await self._start_intent_timer(timer)

            if timer.status != TimerStatus.RUNNING:
                await self.store.update_status(timer.id, TimerStatus.RUNNING)

                # Fire event - done here to only fire if new timer started not
                # existing timer restarted after HA restart
                await self._fire_event(timer.id, TimerEvent.STARTED)

    async def snooze_timer(
        self, timer_id: str, timer_info: TimerInfo
    ) -> tuple[str | None, Timer | None, str]:
        """Snooze expired timer.

        This will set the timer expire to now plus duration on an expired timer
        and set the status to snooze.  Then re-run the timer.
        """
        timer = self.store.timers.get(timer_id)
        if timer and timer.status == TimerStatus.EXPIRED:
            expiry = dt.datetime.now() + dt.timedelta(
                hours=timer_info.hours,
                minutes=timer_info.minutes,
                seconds=timer_info.seconds,
            )
            timer.expires_at = time.mktime(expiry.timetuple())
            timer.extra_info["snooze_duration"] = timer_info.sentence
            await self.store.update_status(timer_id, TimerStatus.SNOOZED)
            await self.start_timer(timer)
            await self._fire_event(timer_id, TimerEvent.SNOOZED)

            # encoded_duration = encode_datetime_to_human(
            #    "TimerInterval", expiry, self.tz
            # )

            return (
                "timer_named_snoozed" if timer.name else "timer_snoozed",
                self.format_timer_output(timer),
            )
        return "timer_error", self.format_timer_output(timer) if timer else None

    async def cancel_timer(
        self,
        timer_id: str | None = None,
        device_id: str | None = None,
        entity_id: str | None = None,
        cancel_all: bool = False,
        just_expired: bool = False,
    ) -> bool:
        """Cancel timer by timer id, device id or all."""
        if timer_id:
            timer_ids = [timer_id] if self.store.timers.get(timer_id) else []
        elif device_id or entity_id:
            if not entity_id:
                entity_id = self._get_entity_id(device_id)
            if entity_id:
                timer_ids = [
                    timer_id
                    for timer_id, timer in self.store.timers.items()
                    if timer.entity_id == entity_id
                ]
            else:
                timer_ids = []
        elif cancel_all:
            timer_ids = self.store.timers.copy().keys()

        if timer_ids:
            for timerid in timer_ids:
                if (
                    just_expired
                    and self.store.timers[timerid].status != TimerStatus.EXPIRED
                ):
                    continue

                if timer := self.store.timers.get(timerid):
                    device_domain = get_mic_device_domain(self.hass, timer.entity_id)
                    if device_domain == "esphome":
                        await self._cancel_intent_timer(timerid)

                if await self.store.cancel_timer(timerid):
                    _LOGGER.debug("Cancelled timer: %s", timerid)
                    if timer_task := self.timer_tasks.pop(timerid, None):
                        if not timer_task.done():
                            timer_task.cancel()

                    return True

        return False

    def get_timers(
        self,
        timer_id: str = "",
        device_id: str = "",
        entity_id: str = "",
        name: str = "",
        include_expired: bool = False,
        sort: bool = True,
    ) -> list[Timer]:
        """Get list of timers.

        Optionally supply timer_id, device_id or entity id to filter the returned list
        """

        # Get list of all or active only timers
        if include_expired:
            timers = [
                {"id": tid, **self.format_timer_output(timer)}
                for tid, timer in self.store.timers.items()
            ]
        else:
            timers = [
                {"id": tid, **self.format_timer_output(timer)}
                for tid, timer in self.store.timers.items()
                if timer.status != TimerStatus.EXPIRED
            ]

        if timer_id:
            timers = [timer for timer in timers if timer["id"] == timer_id]

        elif device_id or entity_id:
            if not entity_id:
                entity_id = self._get_entity_id(device_id)
            timers = [timer for timer in timers if timer["entity_id"] == entity_id]

            # If esphome device, filter by timers registered with timer manager
            # If using stop to cancel alarm on HAVPE, does not use the cancel service
            # and therefore the alarm is left behind in expired state.  So filter out any timers
            # that are not still registered with the intent timer manager
            device_domain = get_mic_device_domain(self.hass, entity_id)
            tm: IntentTimerManager = self.hass.data[TIMER_DATA]
            if device_domain == "esphome":
                timers = [timer for timer in timers if timer["id"] in tm.timers]

            # Filter by name if supplied
            if name:
                # Match on name or plural of name
                name = str(name).strip()
                timers = [
                    timer
                    for timer in timers
                    if timer["name"] == name
                    or str(timer["duration"]).startswith(name)
                    or timer["time"] == name
                ]

        if sort and timers:
            timers = sorted(timers, key=lambda d: d["expiry"]["seconds"])

        return timers

    def get_expiry_from_timerinfo(
        self, timerinfo: TimerInfo | None
    ) -> dt.datetime | None:
        """Decode a string into a TimerTime or TimerInterval."""
        if not timerinfo:
            return None

        if timerinfo.is_time:
            # Make base time
            if timerinfo.timeofday == "pm" and timerinfo.hours < 12:
                timerinfo.hours += 12

            expiry = dt.datetime.now(tz=self.tz).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            expiry += dt.timedelta(
                hours=timerinfo.hours,
                minutes=timerinfo.minutes,
                seconds=timerinfo.seconds,
            )

            # Add days part to datetime
            WEEKDAYS = [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ]
            if timerinfo.dayofweek:
                if timerinfo.dayofweek == "tomorrow":
                    expiry += dt.timedelta(days=1)
                else:
                    days_ahead = (
                        WEEKDAYS.index(timerinfo.dayofweek) - expiry.weekday() + 7
                    ) % 7
                    if days_ahead == 0 and (
                        timerinfo.hours < expiry.hour
                        or (
                            timerinfo.hours == expiry.hour
                            and timerinfo.minutes <= expiry.minute
                        )
                    ):
                        days_ahead = 7
                    expiry += dt.timedelta(days=days_ahead)

            # If time is less than now, add 12 hours if no meridiem or 24 hours if am/pm
            if expiry < dt.datetime.now(tz=self.tz):
                if timerinfo.timeofday:
                    expiry += dt.timedelta(days=1)
                else:
                    expiry += dt.timedelta(hours=12)

            return expiry

        # TimeInfo is interval.  Make timedelta from parts
        return dt.datetime.now(tz=self.tz) + dt.timedelta(
            days=timerinfo.days,
            hours=timerinfo.hours,
            minutes=timerinfo.minutes,
            seconds=timerinfo.seconds,
        )

    async def _fire_event(self, timer_id: int, event_type: TimerEvent):
        """Fire timer event on the event bus."""
        if timer := self.store.timers.get(timer_id):
            event_name = (
                VA_COMMAND_EVENT_PREFIX
                if timer.timer_class == TimerClass.COMMAND
                else VA_EVENT_PREFIX
            ).format(event_type)
            event_data = {"timer_id": timer_id}
            event_data.update(self.format_timer_output(timer))
            self.hass.bus.async_fire(event_name, event_data)
            _LOGGER.debug("Timer event fired: %s - %s", event_name, event_data)

    def _get_entity_id(self, device_id: str) -> str:
        """Ensure entity id."""
        # ensure entity id
        return get_entity_id_from_conversation_device_id(self.hass, device_id)

    def is_duplicate_timer(
        self, entity_id: str, name: str, expires_at: int
    ) -> Timer | None:
        """Return if same timer already exists."""

        # Get timers for device_id
        existing_device_timers = [
            timer_id
            for timer_id, timer in self.store.timers.items()
            if timer.entity_id == entity_id
        ]

        if not existing_device_timers:
            return None

        for timer_id in existing_device_timers:
            timer = self.store.timers[timer_id]
            if timer.expires_at == expires_at:
                return timer
        return None

    def format_timer_output(self, timer: Timer) -> dict[str, Any]:
        """Format timer output."""

        def expires_in_seconds(expires_at: int) -> int:
            """Get expire in time in seconds."""
            return (
                dt.datetime.fromtimestamp(expires_at) - dt.datetime.now()
            ).total_seconds()

        def expires_in_interval(expires_at: int) -> dict[str, Any]:
            """Get expire in time in days, hours, mins, secs tuple."""
            expires_in = math.ceil(expires_in_seconds(expires_at))
            days, remainder = divmod(expires_in, 3600 * 24)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            return {
                "days": days,
                "hours": hours,
                "minutes": minutes,
                "seconds": int(seconds),
            }

        def dynamic_remaining(timer_type: TimerClass, expires_at: int) -> str:
            """Generate dynamic name."""
            return encode_datetime_to_human(
                timer_type, dt.datetime.fromtimestamp(expires_at, self.tz), self.tz
            )

        def make_duration_text(timer_info: dict | TimerInfo) -> str:
            """Generate duration from timer info."""
            if isinstance(timer_info, TimerInfo):
                timer_info = timer_info.__dict__

            d = [
                (k, v)
                for k, v in timer_info.items()
                if k in ["days", "hours", "minutes", "seconds"] and int(v) > 0
            ]
            out = ""
            for idx, e in enumerate(d):
                out += f"{e[1]} {e[0]}"
                if idx == len(d) - 2:
                    out += " and "
                elif idx != len(d) - 1:
                    out += ", "
            return out

        def speak_remaining(timer: Timer) -> str:
            """Generate speech status."""

            # Generate name and class
            name_class = timer.timer_class
            if timer.name:
                name_class = f"{timer.name} {name_class}"
            elif timer.timer_type == "interval":
                name_class = f"{timer.extra_info.get('sentence')} {name_class}"

            output = (
                f"{'an' if name_class[0].lower() in 'aeiou' else 'a'} {name_class} "
            )

            if timer.timer_type == "time":
                output += f"for {dynamic_remaining(timer.timer_type, timer.expires_at)}"
            elif timer.timer_type == "interval":
                output += f"with {dynamic_remaining(timer.timer_type, timer.expires_at)} remaining"

            return output.strip()

        # dt_now = dt.datetime.now(self.tz)
        dt_expiry = dt.datetime.fromtimestamp(timer.expires_at, self.tz)

        return {
            "id": timer.id,
            "entity_id": timer.entity_id,
            "device_id": timer.conversation_device_id,
            "timer_class": timer.timer_class,
            "timer_type": timer.timer_type,
            "name": timer.name,
            "duration": make_duration_text(timer.extra_info["timer_info"])
            if timer.timer_type == TimerType.INTERVAL
            else "",
            "time": get_formatted_time(dt_expiry)
            if timer.timer_type == TimerType.TIME
            else "",
            "expires": dt_expiry,
            "original_expiry": dt.datetime.fromtimestamp(
                timer.original_expires_at, self.tz
            ),
            "pre_expire_warning": timer.pre_expire_warning,
            "expiry": {
                "seconds": math.ceil(expires_in_seconds(timer.expires_at)),
                "interval": expires_in_interval(timer.expires_at),
                # "day": get_named_day(dt_expiry, dt_now),
                "time": get_formatted_time(dt_expiry),
                "day": get_named_day(dt_expiry, dt.datetime.now(self.tz)),
                "text": dynamic_remaining(timer.timer_type, timer.expires_at),
                "speak": speak_remaining(timer),
            },
            "created_at": dt.datetime.fromtimestamp(timer.created_at, self.tz),
            "updated_at": dt.datetime.fromtimestamp(timer.updated_at, self.tz),
            "status": timer.status,
            "extra_info": timer.extra_info,
        }

    async def _wait_for_timer(
        self, timer_id: str, seconds: int, expires_at: int, fire_warning: bool = True
    ) -> None:
        """Sleep until timer is up. Timer is only finished if it hasn't been updated."""
        try:
            await asyncio.sleep(seconds)
            timer = self.store.timers.get(timer_id)
            _LOGGER.debug("Timer finished: %s", timer)
            _LOGGER.debug(
                "Details: %s, %s, %s",
                int(expires_at),
                fire_warning,
                timer.pre_expire_warning,
            )
            if timer and int(timer.expires_at) == int(expires_at):
                if fire_warning and timer.pre_expire_warning:
                    await self._pre_expire_warning(timer_id)
                else:
                    await self._timer_finished(timer_id)
        except asyncio.CancelledError:
            pass  # expected when timer is updated

    async def _pre_expire_warning(self, timer_id: str) -> None:
        """Call event on timer pre_expire_warning and then call expire."""
        timer = self.store.timers[timer_id]

        if timer and timer.status == TimerStatus.RUNNING:
            await self._fire_event(timer_id, TimerEvent.WARNING)

            await asyncio.sleep(timer.pre_expire_warning)
            await self._timer_finished(timer_id)

    async def _timer_finished(self, timer_id: str) -> None:
        """Call event handlers when a timer finishes."""
        _LOGGER.debug("Timer expired: %s", timer_id)
        await self.store.update_status(timer_id, TimerStatus.EXPIRED)
        self.timer_tasks.pop(timer_id, None)

        timer = self.store.timers.get(timer_id)
        device_domain = get_mic_device_domain(self.hass, timer.entity_id)
        if device_domain == "esphome":
            await self._finish_intent_timer(timer_id)
        else:
            await self._fire_event(timer_id, TimerEvent.EXPIRED)

    async def _start_intent_timer(self, timer: Timer, retry: bool = True) -> None:
        """Send intent to VA intent handler."""
        device_id = get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
        orig_total_seconds = round(timer.expires_at - timer.created_at)
        total_seconds = round(
            timer.expires_at - dt.datetime.now(tz=self.tz).timestamp()
        )
        _LOGGER.debug(
            "Sending intent timer for device id: %s for %s seconds",
            device_id,
            total_seconds,
        )

        tm: IntentTimerManager = self.hass.data[TIMER_DATA]
        intent_timer = IntentTimerInfo(
            id=timer.id,
            name=timer.name,
            start_hours=0,
            start_minutes=0,
            start_seconds=orig_total_seconds,
            seconds=orig_total_seconds,
            language=timer.extra_info.get("language", "en"),
            device_id=device_id,
            created_at=timer.created_at_monotonic,
            updated_at=timer.created_at_monotonic,
        )
        _LOGGER.debug(
            "Created intent timer created seconds: %s", intent_timer.created_seconds
        )

        # Fill in area/floor info
        device_registry = dr.async_get(self.hass)
        if device_id and (device := device_registry.async_get(device_id)):
            intent_timer.area_id = device.area_id
            area_registry = ar.async_get(self.hass)
            if device.area_id and (
                area := area_registry.async_get_area(device.area_id)
            ):
                intent_timer.area_name = _normalize_name(area.name)
                intent_timer.floor_id = area.floor_id

        tm.timers[timer.id] = intent_timer
        if (not intent_timer.conversation_command) and (
            intent_timer.device_id in tm.handlers
        ):
            tm.handlers[intent_timer.device_id](TimerEventType.STARTED, intent_timer)
        elif retry:
            self.config.async_create_background_task(
                self.hass,
                self._retry_start_intent_timer(timer),
                name=f"Retry Intent Timer {timer.id}",
            )

    async def _retry_start_intent_timer(self, timer: Timer) -> None:
        """Retry starting intent timer after delay."""
        # TODO: Can we restart timers when device comes back online instead?
        await asyncio.sleep(10)
        await self._start_intent_timer(timer, retry=False)

    async def _finish_intent_timer(self, timer_id: str) -> None:
        """Finish intent timer by timer id."""
        if timer := self.store.timers.get(timer_id):
            device_id = get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
            tm: IntentTimerManager = self.hass.data[TIMER_DATA]
            if timer := tm.timers.pop(timer_id):
                timer.finish()
                if device_id in tm.handlers:
                    tm.handlers[device_id](TimerEventType.FINISHED, timer)

    async def _cancel_intent_timer(self, timer_id: str) -> bool:
        """Cancel intent timer by timer id."""
        if timer := self.store.timers.get(timer_id):
            device_id = get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
            tm: IntentTimerManager = self.hass.data[TIMER_DATA]
            if timer := tm.timers.pop(timer_id):
                timer.cancel()
                if device_id in tm.handlers:
                    tm.handlers[device_id](TimerEventType.CANCELLED, timer)


class TimerManagerServices:
    """Class to hold timer manager service names."""

    ATTR_JUST_EXPIRED = "just_expired"

    SET_TIMER_SERVICE_SCHEMA = vol.Schema(
        {
            vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
            vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
            vol.Required(ATTR_TYPE): str,
            vol.Optional(ATTR_NAME): str,
            vol.Optional(ATTR_LANGUAGE): str,
            vol.Required(ATTR_TIME): str,
            vol.Optional(ATTR_EXTRA): vol.Schema({}, extra=vol.ALLOW_EXTRA),
        }
    )

    CANCEL_TIMER_SERVICE_SCHEMA = vol.Schema(
        {
            vol.Exclusive(ATTR_TIMER_ID, "target"): str,
            vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
            vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
            vol.Exclusive(ATTR_REMOVE_ALL, "target"): bool,
            vol.Optional(ATTR_JUST_EXPIRED): bool,
        }
    )

    SNOOZE_TIMER_SERVICE_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_TIMER_ID): str,
            vol.Required(ATTR_TIME): str,
        }
    )

    GET_TIMERS_SERVICE_SCHEMA = vol.Schema(
        {
            vol.Exclusive(ATTR_TIMER_ID, "target"): str,
            vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
            vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
            vol.Optional(ATTR_NAME): str,
            vol.Optional(ATTR_INCLUDE_EXPIRED, default=False): bool,
        }
    )

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the menu manager services."""
        self.hass = hass

    def register(self):
        """Register menu manager services."""
        # Init services
        self.hass.services.async_register(
            DOMAIN,
            "set_timer",
            self._async_handle_set_timer,
            schema=self.SET_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "snooze_timer",
            self._async_handle_snooze_timer,
            schema=self.SNOOZE_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "cancel_timer",
            self._async_handle_cancel_timer,
            schema=self.CANCEL_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "get_timers",
            self._async_handle_get_timers,
            schema=self.GET_TIMERS_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    def unregister(self):
        """Unregister menu manager services."""
        for service in ["set_timer", "snooze_timer", "cancel_timer", "get_timers"]:
            self.hass.services.async_remove(DOMAIN, service)

    async def decode_time_sentence(
        self, sentence: str, language: str = "en", time_type: str = "time"
    ) -> tuple[None, None]:
        """Decode a time sentence into TimerTime or TimerInterval object."""
        translator = Translator.get(self.hass)
        normaliser = Normaliser(self.hass, locale=language)
        en = await translator.translate_time(sentence, language)
        n = await normaliser.normalise(en, type_hint=time_type)

        if n:
            _LOGGER.debug(
                "Translated (%s) sentence: %s -> %s -> %s", language, sentence, en, n
            )
            return sentence, n

        _LOGGER.warning(
            "Unable to translate (%s) sentence: %s -> %s -> %s",
            language,
            sentence,
            en,
            n,
        )
        return (None, None)

    async def create_response(
        self, response_id: str, timer: Timer | None = None, language: str = "en"
    ) -> str:
        """Create a response string for a timer."""
        translator = Translator.get(self.hass)
        params = {}
        if timer:
            params = {
                "name": timer["name"],
                f"time_{language}": timer["extra_info"].get("sentence", ""),
                "time_en": timer["extra_info"].get("sentence", ""),
                "snooze_duration": timer["extra_info"].get("snooze_duration", ""),
            }
        return await translator.translate_time_response(response_id, params, language)

    async def _async_handle_set_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        timer_type = call.data.get(ATTR_TYPE)
        name = call.data.get(ATTR_NAME)
        timer_time = call.data.get(ATTR_TIME)
        language = call.data.get(ATTR_LANGUAGE, "en")
        extra_data = call.data.get(ATTR_EXTRA)

        if timer_type and str(timer_type).lower() in ["reminder", "alarm"]:
            time_type = "time"
        else:
            time_type = "interval"
            
        # Some STT add additional chars.  This removes those that add - or .
        if timer_time:
            timer_time = timer_time.replace("-", "").replace(".", "")
        
        sentence, timer_info = await self.decode_time_sentence(
            timer_time, language=language, time_type=time_type
        )

        if not timer_info:
            response = await self.create_response("timer_error", language=language)
            return {"response": response}

        if entity_id is None and device_id is None:
            mimic_device = get_mimic_entity_id(self.hass)
            if mimic_device:
                entity_id = mimic_device
                _LOGGER.warning(
                    "Using the set mimic entity %s to set timer as no entity or device id provided to the set timer service",
                    mimic_device,
                )
            else:
                raise vol.InInvalid("entity_id or device_id is required")

        extra_info = {"sentence": sentence}
        if extra_data:
            extra_info.update(extra_data)

        if timer_info:
            tm = TimerManager.get(self.hass)
            response_id, timer = await tm.add_timer(
                timer_class=timer_type,
                device_id=device_id,
                entity_id=entity_id,
                timer_info=timer_info,
                name=name,
                extra_info=extra_info,
            )

            response = await self.create_response(response_id, timer, language)
            _LOGGER.debug("Set timer response: %s", response)
            return {
                "timer_id": timer["id"] if timer else None,
                "timer": timer if timer else None,
                "response": response,
            }

        response = await self.create_response("timer_error", language=language)
        return {"response": response}

    async def _async_handle_snooze_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        timer_id = call.data.get(ATTR_TIMER_ID)
        timer_time = call.data.get(ATTR_TIME)
        language = call.data.get(ATTR_LANGUAGE, "en")

        _, timer_info = await self.decode_time_sentence(
            timer_time, language=language, time_type="interval"
        )

        if not timer_info:
            response = await self.create_response("timer_error", language=language)
            return {"response": response}

        if timer_info:
            tm = TimerManager.get(self.hass)
            response_id, timer = await tm.snooze_timer(
                timer_id,
                timer_info,
            )

            response = await self.create_response(response_id, timer, language)
            _LOGGER.debug("Set timer response: %s", response)
            return {
                "timer_id": timer["id"] if timer else None,
                "timer": timer if timer else None,
                "response": response,
            }
        response = await self.create_response("timer_error", language=language)
        return {"response": response}

    async def _async_handle_cancel_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        timer_id = call.data.get(ATTR_TIMER_ID)
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        cancel_all = call.data.get(ATTR_REMOVE_ALL, False)
        just_expired = call.data.get(self.ATTR_JUST_EXPIRED, False)

        if any([timer_id, entity_id, device_id, cancel_all]):
            tm = TimerManager.get(self.hass)
            result = await tm.cancel_timer(
                timer_id=timer_id,
                device_id=device_id,
                entity_id=entity_id,
                cancel_all=cancel_all,
                just_expired=just_expired,
            )
            response = await self.create_response(
                "timer_cancelled" if result else "timer_not_found"
            )
            return {"response": response}
        return {"error": "no attribute supplied"}

    async def _async_handle_get_timers(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        timer_id = call.data.get(ATTR_TIMER_ID)
        name = call.data.get(ATTR_NAME)
        include_expired = call.data.get(ATTR_INCLUDE_EXPIRED, False)

        tm = TimerManager.get(self.hass)
        result = tm.get_timers(
            timer_id=timer_id,
            device_id=device_id,
            entity_id=entity_id,
            name=name,
            include_expired=include_expired,
        )
        return {"result": result}
