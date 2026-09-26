"""Decoder for time and interval."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from normaliser import TimerInfo


@dataclass
class TimerInterval:
    """Class to hold timer interval data."""

    sentence: str | None = None
    translated: str | None = None
    processed: str | None = None
    class_reason: str | None = None
    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0


@dataclass
class TimerTime:
    """Class to hold timer time data."""

    sentence: str | None = None
    translated: str | None = None
    processed: str | None = None
    class_reason: str | None = None
    day: str | None = None
    meridiem: str | None = None
    time: str | None = None


class Decoder:
    """Decode time and interval strings."""

    def decode(self, build: TimerInfo | None) -> TimerTime | TimerInterval | None:
        """Decode a string into a TimerTime or TimerInterval."""
        if not build:
            return None

        if build.is_time:
            if build.meridiem == "pm" or (build.timeofday == "pm" and build.hours < 12):
                build.hours += 12

            if build.special_hour == "noon":
                build.hours = 12
                build.minutes = 0
            if build.special_hour == "midnight":
                build.hours = 0
                build.minutes = 0

            now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            now += timedelta(
                hours=build.hours, minutes=build.minutes, seconds=build.seconds
            )
            WEEKDAYS = [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ]

            if build.dayofweek:
                if build.dayofweek == "tomorrow":
                    now += timedelta(days=1)
                else:
                    days_ahead = (
                        WEEKDAYS.index(build.dayofweek) - now.weekday() + 7
                    ) % 7
                    if days_ahead == 0 and (
                        build.hours < now.hour
                        or (build.hours == now.hour and build.minutes <= now.minute)
                    ):
                        days_ahead = 7
                    now += timedelta(days=days_ahead)

            return (
                f"{build.dayofweek} at {now.strftime('%H:%M')}"
                if build.dayofweek
                else now.strftime("%H:%M")
            )

        parts = []
        if build.days:
            parts.append(f"{build.days}d")
        if build.hours:
            parts.append(f"{build.hours}h")
        if build.minutes:
            parts.append(f"{build.minutes}m")
        if build.seconds:
            parts.append(f"{build.seconds}s")
        return " ".join(parts)
