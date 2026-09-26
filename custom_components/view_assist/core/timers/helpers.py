"""Helpers for Timers."""

import datetime as dt
import logging
import math
import zoneinfo

from homeassistant.util import dt as dt_util

from .typed import Duration, Timer, TimerClass, TimerInfo, TimerRemainingInfo, TimerType

_LOGGER = logging.getLogger(__name__)

PRE_EXPIRE_WARNING = 10  # seconds

DAYS_OF_THE_WEEK = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def get_formatted_time(timer_dt: dt.datetime, h24format: bool = False) -> str:
    """Format datetime to time."""

    time_format = "%-H:%M" if h24format else "%-I:%M %p"

    if timer_dt.second:
        time_format = "%-H:%M:%S" if h24format else "%-I:%M:%S %p"

    return timer_dt.strftime(time_format)


def get_named_day(timer_dt: dt.datetime, language: str) -> str:
    """Return a named day or date."""
    dt_now = dt_util.now()
    days_diff = (timer_dt.date() - dt_now.date()).days
    if days_diff == 0:
        return "Today"
    if days_diff == 1:
        return "Tomorrow"
    if days_diff < 7:
        return timer_dt.strftime("%A")
    if timer_dt.year == dt_now.year:
        return timer_dt.strftime("%-d %B")
    return timer_dt.strftime("%A, %-d %B %Y")


def make_singular(sentence: str) -> str:
    """Make a time senstence singluar."""
    if sentence[-1:].lower() == "s":
        return sentence[:-1]
    return sentence


class TimerHelpers:
    """Class to hold timer helper functions."""

    @staticmethod
    def get_datetime_from_timestamp(
        timestamp: int, timezone: str | None = None
    ) -> dt.datetime:
        """Decode a timestamp into a datetime with timezone info."""
        if not timezone:
            return dt.datetime.fromtimestamp(timestamp, dt_util.now().tzinfo)
        return dt.datetime.fromtimestamp(timestamp, zoneinfo.ZoneInfo(timezone))

    @staticmethod
    def is_datetime_string(value: str) -> bool:
        """Check if a string is a datetime string."""
        try:
            dt_util.parse_datetime(str(value), raise_on_error=True)
        except ValueError:
            return False
        return True

    @staticmethod
    def get_expiry_from_timerinfo(timerinfo: TimerInfo | None) -> dt.datetime | None:
        """Decode a string into a TimerTime or TimerInterval."""
        if not timerinfo:
            return None

        if timerinfo.is_interval:
            # TimeInfo is interval.  Make timedelta from parts
            return dt_util.now() + dt.timedelta(
                days=timerinfo.days,
                hours=timerinfo.hours,
                minutes=timerinfo.minutes,
                seconds=timerinfo.seconds,
            )

        # Make from datetime
        if timerinfo.datetime:
            return dt.datetime.fromtimestamp(timerinfo.datetime, dt_util.now().tzinfo)

        # Attempt to make expiry from parts.  This is a fallback for when the datetime is not set
        if timerinfo.hours or timerinfo.minutes or timerinfo.seconds:
            expiry = dt_util.now().replace(
                hour=timerinfo.hours,
                minute=timerinfo.minutes,
                second=timerinfo.seconds,
                microsecond=0,
            )
            if (not timerinfo.timeofday and timerinfo.hours < 6) or (
                timerinfo.timeofday == "pm" and timerinfo.hours < 12
            ):
                expiry += dt.timedelta(hours=12)
            elif timerinfo.timeofday == "am" and timerinfo.hours == 12:
                expiry = expiry.replace(hour=0)

            if timerinfo.dayofweek:
                if timerinfo.dayofweek == "tomorrow":
                    expiry += dt.timedelta(days=1)
                else:
                    days_ahead = (
                        DAYS_OF_THE_WEEK.index(timerinfo.dayofweek) - expiry.weekday()
                    ) % 7
                    if days_ahead == 0:
                        days_ahead = 7
                    expiry += dt.timedelta(days=days_ahead)

            # Ensure datetime is in the future.  If not, advance to next occurrence
            if expiry < dt_util.now():
                if timerinfo.timeofday in ["am", "pm"]:
                    expiry += dt.timedelta(days=1)
                else:
                    expiry += dt.timedelta(hours=12)

            return expiry
        return None

    @staticmethod
    def expires_in_seconds(expires_at: int) -> int:
        """Get expire in time in seconds."""
        return (
            TimerHelpers.get_datetime_from_timestamp(expires_at) - dt_util.now()
        ).total_seconds()

    @staticmethod
    def get_remaining_duration_from_datetime(expiry_dt: dt.datetime) -> Duration:
        """Get expire in time in days, hours, mins, secs tuple."""

        expires_in = (expiry_dt - dt_util.now()).total_seconds()
        days, remainder = divmod(expires_in, 3600 * 24)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        return Duration(
            days=days,
            hours=hours,
            minutes=minutes,
            seconds=int(seconds),
        )

    @staticmethod
    def build_remaining_info_from_timer(
        timer: Timer, language: str = "en", h24format: bool = False
    ) -> TimerRemainingInfo:
        """Format timer output."""
        expiry_dt = TimerHelpers.get_datetime_from_timestamp(
            timer.timer_info.expires_at, timer.timer_info.tz
        )

        if expiry_dt < dt_util.now():
            return TimerRemainingInfo()

        duration = TimerHelpers.get_remaining_duration_from_datetime(expiry_dt)
        day = (
            get_named_day(expiry_dt, language)
            if timer.timer_type == TimerType.TIME
            else None
        )
        time_text = (
            get_formatted_time(expiry_dt, h24format)
            if timer.timer_type == TimerType.TIME
            else None
        )
        remaining_text = (
            TimerHelpers.build_datetime_text(expiry_dt, language, h24format)
            if timer.timer_type == TimerType.TIME
            else TimerHelpers.build_duration_text(duration, language)
        )
        return TimerRemainingInfo(
            total_seconds=math.ceil(
                TimerHelpers.expires_in_seconds(timer.timer_info.expires_at)
            ),
            duration=duration,
            time=time_text,
            day=day,
            text=remaining_text,
            speak=TimerHelpers.speak_remaining(timer, remaining_text),
        )

    @staticmethod
    def dynamic_remaining(timer_type: TimerClass, expires_at: int) -> str:
        """Generate dynamic name."""
        return TimerHelpers.encode_datetime_to_human(
            timer_type,
            dt.datetime.fromtimestamp(expires_at, dt_util.now().tzinfo),
            dt_util.now().tzinfo,
        )

    @staticmethod
    def build_timer_info_from_datetime(
        timer_dt: dt.datetime | str,
        timezone: str = "Europe/London",
        language: str = "en",
    ) -> TimerInfo | None:
        """Get TimerInfo from datetime."""

        # Validate that it is a datetime
        if not isinstance(timer_dt, dt.datetime):
            try:
                timer_dt = dt_util.parse_datetime(timer_dt, raise_on_error=True)
                if not timer_dt.tzinfo:
                    timer_dt = dt_util.as_local(timer_dt)
            except ValueError:
                return None

        return TimerInfo(
            days=0,
            hours=timer_dt.hour,
            minutes=timer_dt.minute,
            seconds=timer_dt.second,
            dayofweek=get_named_day(timer_dt, language)
            if timer_dt.date() != dt_util.now().date()
            else "",
            meridiem=timer_dt.strftime("%p").lower(),
            timeofday=timer_dt.strftime("%p").lower(),
            special_hour="",
            datetime=round(timer_dt.timestamp()),
            tz=timezone,
            is_interval=False,
            request_sentence="",
            pattern="",
            normalised_sentence=TimerHelpers.build_datetime_text(timer_dt, language),
            expires_at=round(timer_dt.timestamp()),
            original_expires_at=round(timer_dt.timestamp()),
            pre_expire_warning=PRE_EXPIRE_WARNING,
        )

    @staticmethod
    def build_timer_info_from_duration(
        duration: Duration, timezone: str = "Europe/London", language: str = "en"
    ) -> TimerInfo:
        """Get TimerInfo from duration."""
        timer_info = TimerInfo(
            days=duration.days,
            hours=duration.hours,
            minutes=duration.minutes,
            seconds=duration.seconds,
            dayofweek="",
            meridiem="",
            timeofday="",
            special_hour="",
            tz=timezone,
            is_interval=True,
            request_sentence="",
            pattern="",
        )
        timer_info.normalised_sentence = TimerHelpers.build_duration_text(
            duration, language
        )
        # calculate expiry time from TimerInfo
        expiry = TimerHelpers.get_expiry_from_timerinfo(timer_info)
        expires_unix_ts = round(expiry.timestamp()) if expiry else 0
        timer_info.expires_at = expires_unix_ts
        timer_info.original_expires_at = expires_unix_ts
        timer_info.pre_expire_warning = PRE_EXPIRE_WARNING

        return timer_info

    @staticmethod
    def build_duration_text(duration: Duration, language: str) -> str:
        """Generate duration text from duration."""

        output = ""
        attrs = ["days", "hours", "minutes", "seconds"]
        for attr in attrs:
            value = int(getattr(duration, attr, 0))
            if value > 0:
                if output:
                    output += ", "
                output += f"{value} {attr}"
        if ", " in output:
            last_comma = output.rfind(", ")
            output = output[:last_comma] + " and" + output[last_comma + 1 :]
        return output

    @staticmethod
    def build_datetime_text_from_timestamp(
        timestamp: int, language: str, h24format: bool = False
    ) -> str:
        """Generate datetime text from timestamp."""
        dt_obj = dt.datetime.fromtimestamp(timestamp, dt_util.now().tzinfo)
        return TimerHelpers.build_datetime_text(dt_obj, language, h24format)

    @staticmethod
    def build_datetime_text(
        date_time: dt.datetime, language: str, h24format: bool = False
    ) -> str:
        """Generate datetime text from timer info."""

        # Add local timezone if naive datetime
        if not date_time.tzinfo:
            date_time = dt_util.as_local(date_time)

        output_day_or_date = get_named_day(date_time, language)
        output_time = get_formatted_time(date_time, h24format)
        return f"{output_day_or_date} at {output_time}"

    @staticmethod
    def speak_remaining(timer: Timer, remaining_text: str) -> str:
        """Generate speech status."""

        # Generate name and class
        name_class = timer.timer_class
        if timer.name:
            name_class = f"{timer.name} {name_class}"
        elif timer.timer_type == "interval":
            name_class = f"{timer.timer_info.normalised_sentence} {name_class}"

        output = f"{'an' if name_class[0].lower() in 'aeiou' else 'a'} {name_class} "

        if timer.timer_type == "time":
            output += f"for {remaining_text}"
        elif timer.timer_type == "interval":
            output += f"with {remaining_text} remaining"

        return output.strip()
