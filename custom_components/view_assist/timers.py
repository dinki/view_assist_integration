"""Class to handle timers with persistent storage."""

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import datetime as dt
from enum import StrEnum
import inspect
import logging
import math
import re
import time
from typing import Any
import zoneinfo

import voluptuous as vol
import wordtodigits

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_NAME, ATTR_TIME
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    valid_entity_id,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.util import ulid as ulid_util

from .const import (
    ATTR_EXTRA,
    ATTR_INCLUDE_EXPIRED,
    ATTR_REMOVE_ALL,
    ATTR_TIMER_ID,
    ATTR_TYPE,
    DOMAIN,
)
from .helpers import get_entity_id_from_conversation_device_id, get_mimic_entity_id

from .translations.timers import timers_english

_LOGGER = logging.getLogger(__name__)

SET_TIMER_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
        vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
        vol.Required(ATTR_TYPE): str,
        vol.Optional(ATTR_NAME): str,
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

# Event name prefixes
VA_EVENT_PREFIX = "va_timer_{}"
VA_COMMAND_EVENT_PREFIX = "va_timer_command_{}"
TIMERS = "timers"
TIMERS_STORE_NAME = f"{DOMAIN}.{TIMERS}"

# Translation Imports
WEEKDAYS= {
    "en": timers_english.WEEKDAYS  # Add more languages here
}

SPECIAL_HOURS = {
    "en": timers_english.SPECIAL_HOURS  # Add more languages here
}

HOUR_FRACTIONS = {
    "en": timers_english.HOUR_FRACTIONS  # Add more languages here
}

SPECIAL_AMPM = {
    "en": timers_english.SPECIAL_AMPM  # Add more languages here
}

DIRECT_REPLACE = {
    "en": timers_english.DIRECT_REPLACE  # Add more languages here
}

REFERENCES = {
    "en": timers_english.REFERENCES  # Add more languages here
}

SINGULARS = {
    "en": timers_english.SINGULARS  # Add more languages here
}

REGEXES = {
    "en": timers_english.REGEXES  # Add more languages here
}

REGEX_DAYS = {
    "en": timers_english.REGEX_DAYS  # Add more languages here
}

INTERVAL_DETECTION_REGEX = {
    "en": timers_english.INTERVAL_DETECTION_REGEX  # Add more languages here
}


class TimerClass(StrEnum):
    """Timer class."""

    ALARM = "alarm"
    REMINDER = "reminder"
    TIMER = "timer"
    COMMAND = "command"


@dataclass
class TimerInterval:
    """Timer Interval."""

    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0


@dataclass
class TimerTime:
    """Timer Time."""

    day: str = ""
    hour: int = 0
    minute: int = 0
    second: int = 0
    meridiem: str = ""


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

class TimerLanguage(StrEnum):
    """Language enums."""

    EN = "en"


@dataclass
class Timer:
    """Class to hold timer."""

    timer_class: TimerClass
    original_expires_at: int
    expires_at: int
    timer_type: str = "TimerInterval"
    name: str | None = None
    entity_id: str | None = None
    pre_expire_warning: int = 0
    created_at: int = 0
    updated_at: int = 0
    status: TimerStatus = field(default_factory=TimerStatus.INACTIVE)
    extra_info: dict[str, Any] | None = None
    language: TimerLanguage = field(default_factory=TimerLanguage.EN)


def _is_interval(sentence, language: TimerLanguage) -> bool:
    return re.search(INTERVAL_DETECTION_REGEX[language], sentence) is not None


def _is_super(sentence: str, is_interval: bool, language: TimerLanguage) -> bool:
    if is_interval:
        return re.search(r"\b" + "|".join(HOUR_FRACTIONS[language]), sentence) is not None
    return (
        re.search(
            r"\b(?:" + "|".join(list(HOUR_FRACTIONS[language]) + list(SPECIAL_HOURS[language])) + ")",
            sentence,
        )
        is not None
    )


def _format_interval_numbers(interval_list: list[str | int]) -> list[int]:
    for idx, entry in enumerate(interval_list):
        if not entry:
            interval_list[idx] = 0
        elif isinstance(entry, str) and entry.isnumeric():
            interval_list[idx] = int(entry)
    return interval_list


def _format_time_numbers(time_list: list[str | int]) -> list[int]:
    for idx, entry in enumerate(time_list):
        if idx in [0, 4]:
            time_list[idx] = time_list[idx].lower()
            continue
        if not entry:
            time_list[idx] = 0
        elif isinstance(entry, str) and entry.isnumeric():
            time_list[idx] = int(entry)
    return time_list


def decode_time_sentence(sentence: str, language: TimerLanguage):
    """Decode a time or interval sentence.

    Returns a TimerTime or TimerInterval object
    """
    decoded = None
    is_interval = _is_interval(sentence, language)
    is_super = _is_super(sentence, is_interval, language)

    _LOGGER.debug("%s is of type %s", sentence, "interval" if is_interval else "time")

    # Convert all word numbers to ints
    if not sentence.startswith("three quarters"):
        sentence = wordtodigits.convert(sentence)

    # Direct replace parts of the string to help decoding
    for repl_item, repl_str in DIRECT_REPLACE[language].items():
        if repl_item in sentence:
            sentence = sentence.replace(repl_item, repl_str)

    for idx, regex in REGEXES[language]["interval" if is_interval else "time"].items():
        if is_super and idx == "base":
            continue
        match = re.match(regex, sentence)
        if match and match.group(0):
            decoded = re.findall(regex, sentence)
            if decoded:
                # Make tuple into list
                decoded = list(decoded[0])

                # if day is blank look if we need to populate
                if not is_interval and not decoded[0]:
                    if day_text := re.findall(REGEX_DAYS[language], sentence):
                        decoded[0] = day_text[0].lower()

                # If has special hours, set meridiem
                if decoded[1] in list(SPECIAL_HOURS[language]):
                    decoded[4] = "am" if SPECIAL_HOURS[language][decoded[1]] < 12 else "pm"

                # now iterate and replace text numbers with numbers
                for i, v in enumerate(decoded):
                    if i > 0:
                        with contextlib.suppress(KeyError):
                            decoded[i] = SPECIAL_HOURS[language][v]
                        with contextlib.suppress(KeyError):
                            decoded[i] = HOUR_FRACTIONS[language][v]
                        with contextlib.suppress(KeyError):
                            decoded[i] = SPECIAL_AMPM[language][v]

                # Make time objects
                if is_interval:
                    # Convert interval to TimerInterval
                    decoded = _format_interval_numbers(decoded)
                    return sentence, TimerInterval(*decoded)
                # Handle time
                decoded = _format_time_numbers(decoded)
                if "super" not in idx:
                    # Convert base and alt base time to TimerTime
                    return sentence, TimerTime(*decoded)

                # Handle super time (which is in different format)
                return sentence, TimerTime(
                    day=decoded[0],
                    hour=decoded[3] - 1 if decoded[2] == "to" else decoded[3],
                    minute=60 - decoded[1] if decoded[2] == "to" else decoded[1],
                    second=0,
                    meridiem=decoded[4],
                )
    _LOGGER.warning(
        "Time senstence decoder - Unable to decode: %s -> %s", sentence, None
    )
    return sentence, None


def get_datetime_from_timer_interval(interval: TimerInterval) -> dt.datetime:
    """Return datetime from TimerInterval."""
    date = dt.datetime.now().replace(microsecond=0)
    return date + dt.timedelta(
        days=interval.days,
        hours=interval.hours,
        minutes=interval.minutes,
        seconds=interval.seconds,
    )


def get_datetime_from_timer_time(
    set_time: TimerTime, language: TimerLanguage, context_time: bool = True
) -> dt.datetime:
    """Return datetime from TimerTime object."""

    def _calc_days_add(day: str, dt_now: dt.datetime, language: TimerLanguage) -> int:
        """Get number of days to add for required weekday from now."""
        has_next = False

        # Deal with the likes of next wednesday
        if REFERENCES[language]["next"] in day:
            has_next = True
            day = day.replace(REFERENCES[language]["next"], "").strip()

        if day in WEEKDAYS[language]:
            # monday is weekday 0
            current_weekday = dt_now.weekday()
            set_weekday = WEEKDAYS[language].index(day)

            # Check for 'next' prefix to day or if day less than today (assume next week)
            if set_weekday < current_weekday or has_next:
                return (7 - current_weekday) + set_weekday

            return set_weekday - current_weekday
        if day == REFERENCES[language]["tomorrow"]:  # or "tomorrow" in sentence:
            return 1
        return 0

    dt_now = dt.datetime.now()

    # Set pm hour based on meridiem stating pm
    if set_time.meridiem == "pm" and set_time.hour < 12:
        set_time.hour = set_time.hour + 12

    # Make initial datetime
    timer_dt = dt_now.replace(
        hour=set_time.hour,
        minute=set_time.minute,
        second=set_time.second,
        microsecond=0,
    )

    # Set the timer_dt day
    if add_days := _calc_days_add(set_time.day, timer_dt, language):
        timer_dt = timer_dt + dt.timedelta(days=add_days)

    # Apply fixed context
    if timer_dt < dt_now:
        # If time has passed, move to next matching time
        if set_time.meridiem:
            # Add 24 hours
            timer_dt = timer_dt + dt.timedelta(days=1)
        else:
            timer_dt = timer_dt + dt.timedelta(hours=12)

    # Add optional context - if before 6am and no meridiem, assume PM
    if context_time and timer_dt.hour < 6 and not set_time.meridiem:
        timer_dt = timer_dt + dt.timedelta(hours=12)

    return timer_dt


def get_named_day(timer_dt: dt.datetime, dt_now: dt.datetime, language: TimerLanguage) -> str:
    """Return a named day or date."""
    days_diff = timer_dt.day - dt_now.day
    if days_diff == 0:
        return "Today"
    if days_diff == 1:
        return "Tomorrow"
    if days_diff < 7:
        return f"{WEEKDAYS[language][timer_dt.weekday()]}".title()
    return timer_dt.strftime("%-d %B")


def get_formatted_time(timer_dt: dt.datetime, h24format: bool = False) -> str:
    """Format datetime to time."""

    if h24format:
        if timer_dt.second:
            return timer_dt.strftime("%-H:%M:%S")
        return timer_dt.strftime("%-H:%M")

    if timer_dt.second:
        return timer_dt.strftime("%-I:%M:%S %p")
    return timer_dt.strftime("%-I:%M %p")


def encode_datetime_to_human(
    timer_type: str,
    timer_dt: dt.datetime,
    language: TimerLanguage,
    h24format: bool = False,
) -> str:
    """Encode datetime into human speech sentence."""

    def declension(term: str, qty: int) -> str:
        if qty > 1:
            return f"{term}s"
        return term

    dt_now = dt.datetime.now()
    delta = timer_dt - dt_now
    delta_s = math.ceil(delta.total_seconds())

    if timer_type == "TimerInterval":
        minutes, seconds = divmod(delta_s, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        response = []
        if days:
            response.append(f"{days} {declension(SINGULARS[language]['day'], days)}")
        if hours:
            response.append(f"{hours} {declension(SINGULARS[language]['hour'], hours)}")
        if minutes:
            response.append(f"{minutes} {declension(SINGULARS[language]['minute'], minutes)}")
        if seconds:
            response.append(f"{seconds} {declension(SINGULARS[language]['second'], seconds)}")

        # Now create sentence
        duration: str = ""
        for i, entry in enumerate(response):
            if i == len(response) - 1 and duration:
                duration += f" {REFERENCES[language]['and']} " + entry
            else:
                duration += " " + entry

        return duration.strip()

    if timer_type == "TimerTime":
        # do date bit - today, tomorrow, day of week if in next 7 days, date
        output_date = get_named_day(timer_dt, dt_now, language)
        output_time = get_formatted_time(timer_dt, h24format)
        return f"{output_date} {REFERENCES[language]['at']} {output_time}"

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
            stored = await self.migrate(stored)
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


class VATimers:
    """Class to handle VA timers."""

    def __init__(self, hass: HomeAssistant, config: ConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.tz: zoneinfo.ZoneInfo = zoneinfo.ZoneInfo(self.hass.config.time_zone)

        self.store = VATimerStore(hass)
        self.timer_tasks: dict[str, asyncio.Task] = {}

        # Init services
        self.hass.services.async_register(
            DOMAIN,
            "set_timer",
            self._async_handle_set_timer,
            schema=SET_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "snooze_timer",
            self._async_handle_snooze_timer,
            schema=SNOOZE_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "cancel_timer",
            self._async_handle_cancel_timer,
            schema=CANCEL_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "get_timers",
            self._async_handle_get_timers,
            schema=GET_TIMERS_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    async def _async_handle_set_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        timer_type = call.data.get(ATTR_TYPE)
        name = call.data.get(ATTR_NAME)
        timer_time = call.data.get(ATTR_TIME)
        extra_data = call.data.get(ATTR_EXTRA)

        sentence, timer_info = decode_time_sentence(timer_time)
        _LOGGER.debug("Time decode: %s -> %s", sentence, timer_info)
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
            timer_id, timer, response = await self.add_timer(
                timer_class=timer_type,
                device_or_entity_id=entity_id if entity_id else device_id,
                timer_info=timer_info,
                name=name,
                extra_info=extra_info,
            )

            return {"timer_id": timer_id, "timer": timer, "response": response}
        return {"error": "unable to decode time or interval information"}

    async def _async_handle_snooze_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        timer_id = call.data.get(ATTR_TIMER_ID)
        timer_time = call.data.get(ATTR_TIME)

        _, timer_info = decode_time_sentence(timer_time)

        if timer_info:
            timer_id, timer, response = await self.snooze_timer(
                timer_id,
                timer_info,
            )

            return {"timer_id": timer_id, "timer": timer, "response": response}
        return {"error": "unable to decode time or interval information"}

    async def _async_handle_cancel_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        timer_id = call.data.get(ATTR_TIMER_ID)
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        cancel_all = call.data.get(ATTR_REMOVE_ALL, False)

        if any([timer_id, entity_id, device_id, cancel_all]):
            result = await self.cancel_timer(
                timer_id=timer_id,
                device_or_entity_id=entity_id if entity_id else device_id,
                cancel_all=cancel_all,
            )
            return {"result": result}
        return {"error": "no attribute supplied"}

    async def _async_handle_get_timers(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        timer_id = call.data.get(ATTR_TIMER_ID)
        name = call.data.get(ATTR_NAME)
        include_expired = call.data.get(ATTR_INCLUDE_EXPIRED, False)

        result = self.get_timers(
            timer_id=timer_id,
            device_or_entity_id=entity_id if entity_id else device_id,
            name=name,
            include_expired=include_expired,
        )
        return {"result": result}

    async def load(self):
        """Load data store."""
        await self.store.load()

        if self.store.timers:
            # Removed any in expired status on restart as event already got fired
            expired_timers = [
                timer_id
                for timer_id, timer in self.store.timers.items()
                if timer.status == TimerStatus.EXPIRED
            ]
            for timer_id in expired_timers:
                self.store.timers.pop(timer_id, None)

            for timer_id, timer in self.store.timers.items():
                await self.start_timer(timer_id, timer)

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

    def _ensure_entity_id(self, device_or_entity_id: str) -> str:
        """Ensure entity id."""
        # ensure entity id
        if valid_entity_id(device_or_entity_id.lower()):
            entity_id = device_or_entity_id
        else:
            entity_id = get_entity_id_from_conversation_device_id(
                self.hass, device_or_entity_id
            )
        return entity_id

    def is_duplicate_timer(self, entity_id: str, name: str, expires_at: int) -> bool:
        """Return if same timer already exists."""

        # Get timers for device_id
        existing_device_timers = [
            timer_id
            for timer_id, timer in self.store.timers.items()
            if timer.entity_id == entity_id
        ]

        if not existing_device_timers:
            return False

        for timer_id in existing_device_timers:
            timer = self.store.timers[timer_id]
            if timer.expires_at == expires_at:
                return True
        return False

    async def add_timer(
        self,
        timer_class: TimerClass,
        device_or_entity_id: str,
        timer_info: TimerTime | TimerInterval,
        name: str | None = None,
        pre_expire_warning: int = 10,
        start: bool = True,
        extra_info: dict[str, Any] | None = None,
        language: TimerLanguage = TimerLanguage.EN,
    ) -> tuple:
        """Add timer to store."""

        entity_id = self._ensure_entity_id(device_or_entity_id)

        if not entity_id:
            raise vol.Invalid("Invalid device or entity id")

        timer_id = ulid_util.ulid_now()

        # calculate expiry time from TimerTime or TimerInterval
        timer_info_class = timer_info.__class__.__name__
        if timer_info_class == "TimerTime":
            expiry = get_datetime_from_timer_time(timer_info, language)
        elif timer_info_class == "TimerInterval":
            expiry = get_datetime_from_timer_interval(timer_info)
        else:
            raise TypeError("Not a valid time or interval object")

        expires_unix_ts = time.mktime(expiry.timetuple())
        time_now_unix = time.mktime(dt.datetime.now().timetuple())

        if not self.is_duplicate_timer(entity_id, name, expires_unix_ts):
            # Add timer_info to extra_info
            extra_info["timer_info"] = timer_info

            timer = Timer(
                timer_class=timer_class.lower(),
                timer_type=timer_info_class,
                original_expires_at=expires_unix_ts,
                expires_at=expires_unix_ts,
                name=name,
                entity_id=entity_id,
                pre_expire_warning=pre_expire_warning,
                created_at=time_now_unix,
                updated_at=time_now_unix,
                status=TimerStatus.INACTIVE,
                extra_info=extra_info,
                language=language,
            )

            self.store.timers[timer_id] = timer
            await self.store.save()

            if start:
                await self.start_timer(timer_id, timer)

            encoded_time = encode_datetime_to_human(timer_info_class, expiry, language)
            return timer_id, self.format_timer_output(timer), encoded_time

        return None, None, "already exists"

    async def start_timer(self, timer_id: str, timer: Timer):
        """Start timer running."""

        time_now_unix = time.mktime(dt.datetime.now().timetuple())
        total_seconds = timer.expires_at - time_now_unix

        # Fire event if total seconds -ve
        # likely caused by timer expiring during restart
        if total_seconds < 1:
            await self._timer_finished(timer_id)
        else:
            if timer.pre_expire_warning and timer.pre_expire_warning >= total_seconds:
                # Create task to wait for timer duration with no warning
                self.timer_tasks[timer_id] = self.config.async_create_background_task(
                    self.hass,
                    self._wait_for_timer(
                        timer_id, total_seconds, timer.updated_at, fire_warning=False
                    ),
                    name=f"Timer {timer_id}",
                )
                _LOGGER.debug(
                    "Started %s timer for %ss, with no warning event",
                    timer.name,
                    total_seconds,
                )
            else:
                # Create task to wait for timer duration minus any pre_expire_warning time
                self.timer_tasks[timer_id] = self.config.async_create_background_task(
                    self.hass,
                    self._wait_for_timer(
                        timer_id,
                        total_seconds - timer.pre_expire_warning,
                        timer.updated_at,
                    ),
                    name=f"Timer {timer_id}",
                )
                _LOGGER.debug(
                    "Started %s timer for %ss, with warning event at %ss",
                    timer.name,
                    total_seconds,
                    total_seconds - timer.pre_expire_warning,
                )

            # Set timer status
            if timer.status == TimerStatus.SNOOZED:
                return

            if timer.status != TimerStatus.RUNNING:
                await self.store.update_status(timer_id, TimerStatus.RUNNING)

                # Fire event - done here to only fire if new timer started not
                # existing timer restarted after HA restart
                await self._fire_event(timer_id, TimerEvent.STARTED)

    async def snooze_timer(self, timer_id: str, duration: TimerInterval):
        """Snooze expired timer.

        This will set the timer expire to now plus duration on an expired timer
        and set the status to snooze.  Then re-run the timer.
        """
        timer = self.store.timers.get(timer_id)
        if timer and timer.status == TimerStatus.EXPIRED:
            expiry = get_datetime_from_timer_interval(duration)
            timer.expires_at = time.mktime(expiry.timetuple())
            timer.extra_info["snooze_duration"] = duration
            await self.store.update_status(timer_id, TimerStatus.SNOOZED)
            await self.start_timer(timer_id, timer)
            await self._fire_event(timer_id, TimerEvent.SNOOZED)

            encoded_duration = encode_datetime_to_human("TimerInterval", expiry, timer.language)

            return timer_id, timer.to_dict(), encoded_duration
        return None, None, "unable to snooze"

    async def cancel_timer(
        self,
        timer_id: str | None = None,
        device_or_entity_id: str | None = None,
        cancel_all: bool = False,
    ) -> bool:
        """Cancel timer by timer id, device id or all."""
        if timer_id:
            timer_ids = [timer_id] if self.store.timers.get(timer_id) else []
        elif device_or_entity_id:
            if entity_id := self._ensure_entity_id(device_or_entity_id):
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
        device_or_entity_id: str = "",
        name: str = "",
        include_expired: bool = False,
        sort: bool = True,
    ) -> list[Timer]:
        """Get list of timers.

        Optionally supply timer_id or device_id to filter the returned list
        """

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

        elif device_or_entity_id:
            if entity_id := self._ensure_entity_id(device_or_entity_id):
                if name:
                    # If device id and name
                    timers = [
                        timer
                        for timer in timers
                        if timer["entity_id"] == entity_id and timer["name"] == name
                    ]
                else:
                    timers = [
                        timer for timer in timers if timer["entity_id"] == entity_id
                    ]
            else:
                timers = []

        elif name:
            timers = [timer for timer in timers if timer["name"] == name]

        if sort and timers:
            timers = sorted(timers, key=lambda d: d["expiry"]["seconds"])

        return timers

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

        def dynamic_remaining(timer_type: TimerClass, expires_at: int, language: TimerLanguage) -> str:
            """Generate dynamic name."""
            return encode_datetime_to_human(
                timer_type,
                dt.datetime.fromtimestamp(expires_at),
                language
            )

        dt_now = dt.datetime.now(self.tz)
        dt_expiry = dt.datetime.fromtimestamp(timer.expires_at, self.tz)

        return {
            "entity_id": timer.entity_id,
            "timer_class": timer.timer_class,
            "timer_type": timer.timer_type,
            "name": timer.name,
            "expires": dt_expiry,
            "original_expiry": dt.datetime.fromtimestamp(
                timer.original_expires_at, self.tz
            ),
            "pre_expire_warning": timer.pre_expire_warning,
            "expiry": {
                "seconds": math.ceil(expires_in_seconds(timer.expires_at)),
                "interval": expires_in_interval(timer.expires_at),
                "day": get_named_day(dt_expiry, dt_now, timer.language),
                "time": get_formatted_time(dt_expiry),
                "text": dynamic_remaining(timer.timer_type, timer.expires_at, timer.language),
            },
            "created_at": dt.datetime.fromtimestamp(timer.created_at, self.tz),
            "updated_at": dt.datetime.fromtimestamp(timer.updated_at, self.tz),
            "status": timer.status,
            "extra_info": timer.extra_info,
            "language": timer.language,
        }

    async def _wait_for_timer(
        self, timer_id: str, seconds: int, updated_at: int, fire_warning: bool = True
    ) -> None:
        """Sleep until timer is up. Timer is only finished if it hasn't been updated."""
        try:
            await asyncio.sleep(seconds)
            timer = self.store.timers.get(timer_id)
            if timer and timer.updated_at == updated_at:
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

        await self._fire_event(timer_id, TimerEvent.EXPIRED)
        await self.store.update_status(timer_id, TimerStatus.EXPIRED)
        self.timer_tasks.pop(timer_id, None)
