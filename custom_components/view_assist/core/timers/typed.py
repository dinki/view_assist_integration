"""VA Timers data structures."""

from dataclasses import dataclass, field, fields
import datetime as dt
from enum import StrEnum
from typing import Any
import zoneinfo

from homeassistant.util import dt as dt_util

from ...const import DOMAIN  # noqa: TID252

# Event name prefixes
VA_EVENT_PREFIX = "va_timer_{}"
VA_COMMAND_EVENT_PREFIX = "va_timer_command_{}"
TIMERS = "timers"
# Interval between repeated reminder announcements while a reminder is sounding
REMINDER_ANNOUNCE_INTERVAL = 30
TIMERS_STORE_NAME = f"{DOMAIN}.{TIMERS}"
WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


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
    SOUNDING = "sounding"
    SNOOZED = "snoozed"


class TimerEvent(StrEnum):
    """Event enums."""

    STARTED = "started"
    WARNING = "warning"
    EXPIRED = "expired"
    SNOOZED = "snoozed"
    CANCELLED = "cancelled"


def ignore_extra_keys(cls):
    """Decorator to ignore extra keys in dataclass initialization."""
    original_init = cls.__init__

    def new_init(self, *args, **kwargs):
        expected_fields = {field.name for field in fields(cls)}
        cleaned_kwargs = {
            key: value for key, value in kwargs.items() if key in expected_fields
        }
        original_init(self, *args, **cleaned_kwargs)

    cls.__init__ = new_init
    return cls


@ignore_extra_keys
@dataclass
class Duration:
    """Class to hold duration."""

    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert Duration to dictionary."""
        return {
            "days": self.days,
            "hours": self.hours,
            "minutes": self.minutes,
            "seconds": self.seconds,
        }


@ignore_extra_keys
@dataclass
class TimerInfo:
    """Timer information class."""

    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    dayofweek: str = ""
    meridiem: str = ""
    timeofday: str = ""
    special_hour: str = ""
    datetime: int = 0
    time: str = ""
    tz: str = "UTC"
    is_interval: bool = False
    request_sentence: str = ""
    pattern: str = ""
    normalised_sentence: str = ""
    expires_at: int = 0
    original_expires_at: int = 0
    pre_expire_warning: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert TimerInfo to dictionary."""
        return {
            "days": self.days,
            "hours": self.hours,
            "minutes": self.minutes,
            "seconds": self.seconds,
            "dayofweek": self.dayofweek,
            "meridiem": self.meridiem,
            "timeofday": self.timeofday,
            "special_hour": self.special_hour,
            "datetime": dt.datetime.fromtimestamp(
                self.datetime, zoneinfo.ZoneInfo(self.tz)
            )
            if self.datetime
            else None,
            "time": dt.datetime.fromtimestamp(
                self.datetime, zoneinfo.ZoneInfo(self.tz)
            ).strftime("%H:%M")
            if self.datetime
            else None,
            "timezone": self.tz,
            "is_interval": self.is_interval,
            "request_sentence": self.request_sentence,
            "pattern": self.pattern,
            "normalised_sentence": self.normalised_sentence,
            "expires_at": dt.datetime.fromtimestamp(
                self.expires_at, zoneinfo.ZoneInfo(self.tz)
            ),
            "original_expires_at": dt.datetime.fromtimestamp(
                self.original_expires_at, zoneinfo.ZoneInfo(self.tz)
            ),
            "pre_expire_warning": self.pre_expire_warning,
        }


@ignore_extra_keys
@dataclass
class TimerRemainingInfo:
    """Class to hold timer expiry."""

    total_seconds: int = 0
    duration: Duration | None = None
    time: str | None = None
    day: str | None = None
    text: str | None = None
    speak: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert TimerRemainingInfo to dictionary."""
        return {
            "total_seconds": self.total_seconds,
            "duration": self.duration.to_dict() if self.duration else None,
            "time": self.time,
            "day": self.day,
            "text": self.text,
            "speak": self.speak,
        }


@ignore_extra_keys
@dataclass
class SnoozeInfo:
    """Class to hold snooze information."""

    count: int = 0
    duration: Duration | None = None
    snoozed_at: int | None = None
    sentence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert SnoozeInfo to dictionary."""
        return {
            "count": self.count,
            "duration": self.duration,
            "snoozed_at": self.snoozed_at,
            "sentence": self.sentence,
        }


@ignore_extra_keys
@dataclass
class Timer:
    """Class to hold timer."""

    id: str
    timer_class: TimerClass
    timer_type: TimerType = field(default_factory=TimerType.INTERVAL)
    source: str | None = None
    name: str | None = None
    entity_id: str | None = None
    conversation_device_id: str | None = None
    status: TimerStatus = field(default_factory=TimerStatus.INACTIVE)
    created_at: int = 0
    created_at_monotonic: int = 0
    updated_at: int = 0
    timer_info: TimerInfo | None = None
    remaining_info: TimerRemainingInfo | None = None
    snooze_info: SnoozeInfo | None = None
    extra_info: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert Timer to dictionary."""
        return {
            "id": self.id,
            "timer_class": self.timer_class,
            "timer_type": self.timer_type,
            "source": self.source,
            "name": self.name,
            "entity_id": self.entity_id,
            "conversation_device_id": self.conversation_device_id,
            "status": self.status,
            "created_at": dt.datetime.fromtimestamp(
                self.created_at, zoneinfo.ZoneInfo(self.timer_info.tz)
            )
            if self.timer_info and self.timer_info.tz
            else dt.datetime.fromtimestamp(self.created_at, dt_util.now().tzinfo),
            "created_at_monotonic": self.created_at_monotonic,
            "updated_at": dt.datetime.fromtimestamp(
                self.updated_at, zoneinfo.ZoneInfo(self.timer_info.tz)
            )
            if self.timer_info and self.timer_info.tz
            else dt.datetime.fromtimestamp(self.updated_at, dt_util.now().tzinfo),
            "timer_info": self.timer_info.to_dict() if self.timer_info else None,
            "remaining_info": self.remaining_info.to_dict()
            if self.remaining_info
            else None,
            "snooze_info": self.snooze_info.to_dict() if self.snooze_info else None,
            "extra_info": self.extra_info,
        }
