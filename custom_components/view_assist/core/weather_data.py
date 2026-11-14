"""Weather data for the View Assist weather intents.

Reads any weather.* entity. The entity's supported_features says which forecast types it
offers: daily (1), hourly (2) and twice daily (4). Forecasts are fetched with
weather.get_forecasts on each request and are never stored.

Every provider is read the same way:

- A day's high is the highest forecast temperature from 06:00 to 18:00 local time.
- A night's low is the lowest forecast temperature from 18:00 the evening before to
  06:00 that morning. "Tomorrow's low" and "tonight's low" are the same night, the one
  that ends on tomorrow morning.
- Hourly data is used first, then twice daily, then the provider's own daily values.
  A step is only used when the one before it has no data for the window.
- Times already past are ignored, so a period that is partly over uses what is left.

Speech text is not written here. Functions return numbers and band keys, and the
sentence file turns them into words.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.weather import WeatherEntityFeature
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

WEATHER_DOMAIN = "weather"
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
# Local hours for each part of the day. "night" and "all" are built in part_window.
PART_HOURS = {
    "morning": (6, 12),
    "afternoon": (12, 18),
    "evening": (18, 22),
    "day": (6, 18),
}
# A twice daily entry covers the 12 hours after its start time
TWICE_DAILY_SPAN = timedelta(hours=12)

RAIN_CONDITIONS = frozenset({"rainy", "pouring", "lightning-rainy"})
SNOW_CONDITIONS = frozenset({"snowy", "snowy-rainy"})

FORECAST_FEATURES = {
    "daily": WeatherEntityFeature.FORECAST_DAILY,
    "hourly": WeatherEntityFeature.FORECAST_HOURLY,
    "twice_daily": WeatherEntityFeature.FORECAST_TWICE_DAILY,
}

# Converts the wind speed units Home Assistant uses to miles per hour
WIND_TO_MPH = {
    "mph": 1.0,
    "km/h": 0.621371,
    "m/s": 2.236936,
    "kn": 1.150779,
    "ft/s": 0.681818,
}


@dataclass(frozen=True, slots=True)
class Window:
    """A span of local time. The start is included and the end is not."""

    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        """Return True if the moment falls inside the window."""
        return self.start <= moment < self.end

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """Return True if the span from start to end touches the window."""
        return start < self.end and end > self.start


@dataclass(frozen=True, slots=True)
class Entry:
    """One forecast point with its start time in local time."""

    start: datetime
    condition: str | None
    temperature: float | None
    templow: float | None
    precip_prob: float | None


@dataclass(slots=True)
class Forecasts:
    """Forecast points for an entity, grouped by type."""

    daily: list[Entry] = field(default_factory=list)
    hourly: list[Entry] = field(default_factory=list)
    twice_daily: list[Entry] = field(default_factory=list)

    def has_any(self) -> bool:
        """Return True if any forecast point was returned."""
        return bool(self.daily or self.hourly or self.twice_daily)


@dataclass(slots=True)
class Measure:
    """A temperature for a window, with the forecast type it came from."""

    value: float | None = None
    source: str | None = None


@dataclass(slots=True)
class Chance:
    """Rain or snow chance for a window."""

    percent: float | None
    conditions: list[str]
    band: str
    seen: int


# ---------------------------------------------------------------------------
# Supported forecast types
# ---------------------------------------------------------------------------


def supported_types(state: State) -> list[str]:
    """Return the forecast types this entity supports, from supported_features."""
    features = int(state.attributes.get("supported_features") or 0)
    return [name for name, flag in FORECAST_FEATURES.items() if features & flag]


# ---------------------------------------------------------------------------
# Time windows and days
# ---------------------------------------------------------------------------


def local_at(day: date, hour: int) -> datetime:
    """Return the local datetime at the given hour of a day."""
    return dt_util.start_of_local_day(day) + timedelta(hours=hour)


def part_window(day: date, part: str) -> Window:
    """Return the window for a part of a day.

    "night" is the night that starts on the evening of the day, through to the
    morning after. "all" is the whole calendar day.
    """
    if part == "night":
        return Window(local_at(day, 18), local_at(day + timedelta(days=1), 6))
    if part == "all":
        return Window(local_at(day, 0), local_at(day + timedelta(days=1), 0))
    start_hour, end_hour = PART_HOURS[part]
    return Window(local_at(day, start_hour), local_at(day, end_hour))


def night_low_window(day: date) -> Window:
    """Return the night that ends on the morning of the given day."""
    return Window(
        local_at(day - timedelta(days=1), 18),
        local_at(day, 6),
    )


def resolve_day(label: str | None, today: date) -> date | None:
    """Turn a day label into a date, or None if it is not recognised."""
    label = label or "today"
    if label in ("today", "tonight"):
        return today
    if label == "tomorrow":
        return today + timedelta(days=1)
    if label == "day after tomorrow":
        return today + timedelta(days=2)
    if label in WEEKDAYS:
        # The next occurrence, which is today if today is that weekday
        ahead = (WEEKDAYS.index(label) - today.weekday()) % 7
        return today + timedelta(days=ahead)
    return None


# ---------------------------------------------------------------------------
# Reading forecasts
# ---------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    """Return a value as a float, or None if it is missing or not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None) -> int | None:
    """Round a value for speech, keeping None."""
    return None if value is None else round(value)


def _parse_entries(raw: Any, forecast_type: str) -> list[Entry]:
    """Turn the raw forecast list from weather.get_forecasts into entries."""
    entries: list[Entry] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        stamp = dt_util.parse_datetime(str(item.get("datetime") or ""))
        if stamp is None:
            _LOGGER.debug("Skipping forecast point with no usable time: %s", item)
            continue
        if forecast_type == "daily":
            # A daily point names a date. Take that date as the provider gives it,
            # so a point stamped at midnight UTC still means that calendar day.
            start = dt_util.start_of_local_day(stamp.date())
        else:
            start = dt_util.as_local(stamp)
        entries.append(
            Entry(
                start=start,
                condition=item.get("condition"),
                temperature=_number(item.get("temperature")),
                templow=_number(item.get("templow")),
                precip_prob=_number(item.get("precipitation_probability")),
            )
        )
    return sorted(entries, key=lambda entry: entry.start)


async def async_get_forecasts(hass: HomeAssistant, state: State) -> Forecasts:
    """Fetch every forecast type the entity supports, for this request only."""
    forecasts = Forecasts()
    for forecast_type in supported_types(state):
        try:
            response = await hass.services.async_call(
                WEATHER_DOMAIN,
                "get_forecasts",
                {"entity_id": state.entity_id, "type": forecast_type},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Could not get %s forecast from %s: %s",
                forecast_type,
                state.entity_id,
                err,
            )
            continue
        raw = ((response or {}).get(state.entity_id) or {}).get("forecast")
        setattr(forecasts, forecast_type, _parse_entries(raw, forecast_type))
    return forecasts


# ---------------------------------------------------------------------------
# Bands (the thresholds from forecastnotes.yaml)
# ---------------------------------------------------------------------------


def chance_band(percent: float | None, conditions: list[str]) -> str:
    """Return the rain or snow band: none, slight, scattered, likely or heavy.

    With no probability from the provider, the band comes from the conditions.
    """
    if percent is None:
        found = set(conditions)
        if "pouring" in found:
            return "heavy"
        if found & (RAIN_CONDITIONS | SNOW_CONDITIONS):
            return "likely"
        if "lightning" in found:
            return "scattered"
        return "none"
    if percent <= 10:
        return "none"
    if percent <= 30:
        return "slight"
    if percent <= 60:
        return "scattered"
    if percent <= 80:
        return "likely"
    return "heavy"


def wind_band(speed: float | None, unit: str | None) -> str | None:
    """Return the wind band, using the miles per hour thresholds from the notes."""
    if speed is None or unit not in WIND_TO_MPH:
        return None
    mph = speed * WIND_TO_MPH[unit]
    if mph <= 3:
        return "calm"
    if mph <= 7:
        return "light"
    if mph <= 12:
        return "gentle"
    if mph <= 18:
        return "moderate"
    if mph <= 24:
        return "breezy"
    return "strong"


def comfort_band(dew_point: float | None, unit: str | None) -> str | None:
    """Return the comfort band from the dew point, using the Fahrenheit thresholds."""
    if dew_point is None:
        return None
    if unit == "°F":
        fahrenheit = dew_point
    elif unit == "°C":
        fahrenheit = dew_point * 9 / 5 + 32
    else:
        return None
    if fahrenheit < 55:
        return "dry"
    if fahrenheit < 60:
        return "pleasant"
    if fahrenheit < 65:
        return "sticky"
    if fahrenheit < 70:
        return "muggy"
    return "oppressive"


def uv_band(uv: float | None) -> str | None:
    """Return the UV band from the notes."""
    if uv is None:
        return None
    if uv <= 2:
        return "low"
    if uv <= 5:
        return "sunscreen"
    if uv <= 7:
        return "shade"
    return "indoors"


# ---------------------------------------------------------------------------
# Windows: temperatures and chances
# ---------------------------------------------------------------------------


def _upcoming(entries: list[Entry], window: Window, now: datetime) -> list[Entry]:
    """Return entries in the window that start at or after the current hour."""
    floor = now.replace(minute=0, second=0, microsecond=0)
    return [e for e in entries if window.contains(e.start) and e.start >= floor]


def window_temperature(
    fc: Forecasts,
    window: Window,
    now: datetime,
    want: str,
    day: date,
) -> Measure:
    """Return the high or low for a window, using the first source with data.

    day is the calendar day the answer is for. It selects the provider's own
    daily value when there is no hourly or twice daily data.
    """
    pick = max if want == "high" else min

    twice = [
        e.temperature
        for e in fc.twice_daily
        if window.contains(e.start) and e.temperature is not None
    ]
    if twice:
        return Measure(pick(twice), "twice_daily")

    hours = [
        e.temperature
        for e in _upcoming(fc.hourly, window, now)
        if e.temperature is not None
    ]
    if hours:
        return Measure(pick(hours), "hourly")

    for entry in fc.daily:
        if entry.start.date() != day:
            continue
        value = entry.temperature if want == "high" else entry.templow
        if value is not None:
            return Measure(value, "daily")

    return Measure()


def window_chance(fc: Forecasts, window: Window, now: datetime, day: date) -> Chance:
    """Return the rain or snow chance for a window, from the first source with data."""
    hours = _upcoming(fc.hourly, window, now)
    twice = [
        e
        for e in fc.twice_daily
        if window.overlaps(e.start, e.start + TWICE_DAILY_SPAN)
    ]
    daily = [e for e in fc.daily if e.start.date() == day]
    used = hours or twice or daily

    probs = [e.precip_prob for e in used if e.precip_prob is not None]
    conditions = [e.condition for e in used if e.condition]
    percent = max(probs) if probs else None
    return Chance(
        percent=percent,
        conditions=conditions,
        band=chance_band(percent, conditions),
        seen=len(used),
    )


def precip_word(conditions: list[str], asked: str | None) -> str:
    """Return "rain" or "snow" for the answer."""
    if asked in ("rain", "snow"):
        return asked
    found = set(conditions)
    if found & SNOW_CONDITIONS and not found & RAIN_CONDITIONS:
        return "snow"
    return "rain"


def dominant_condition(conditions: list[str]) -> str | None:
    """Return the most common condition in a window."""
    if not conditions:
        return None
    return Counter(conditions).most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Slot builders (called by the handlers)
# ---------------------------------------------------------------------------


def error_slots(code: str, name: str | None = None) -> dict[str, Any]:
    """Return speech slots for an error."""
    return {"error": code, "name": name}


def current_slots(state: State) -> dict[str, Any]:
    """Return speech slots for the current conditions of an entity."""
    attrs = state.attributes
    temperature = _number(attrs.get("temperature"))
    feels = _number(attrs.get("apparent_temperature"))
    wind = _number(attrs.get("wind_speed"))
    wind_unit = attrs.get("wind_speed_unit")
    humidity = _number(attrs.get("humidity"))
    uv = _number(attrs.get("uv_index"))
    dew = _number(attrs.get("dew_point"))
    unit = attrs.get("temperature_unit")

    # Feels like is only spoken when it differs from the temperature by 2 or more
    feels_shown = None
    if feels is not None and temperature is not None and abs(feels - temperature) >= 2:
        feels_shown = feels

    return {
        "error": None,
        "name": None,
        "condition": state.state,
        "temperature": _round(temperature),
        "feels_like": _round(feels_shown),
        "humidity": _round(humidity),
        "wind": _round(wind),
        "wind_unit": wind_unit,
        "wind_band": wind_band(wind, wind_unit),
        "uv": _round(uv),
        "uv_band": uv_band(uv),
        "comfort": comfort_band(dew, unit),
    }


def forecast_slots(
    fc: Forecasts,
    measure: str,
    day_label: str,
    day: date | None,
    now: datetime,
) -> dict[str, Any]:
    """Return speech slots for a high, a low or an outlook for a day."""
    slots: dict[str, Any] = {
        **error_slots(None),
        "measure": measure,
        "day": day_label,
        "value": None,
        "high": None,
        "low": None,
        "condition": None,
        "rain_band": "none",
        "rain_word": "rain",
        "rain_chance": None,
    }
    if day is None or not fc.has_any():
        return {**slots, "error": "no_data"}

    if measure == "high":
        window = part_window(day, "day")
        if window.end <= now:
            return {**slots, "error": "passed"}
        measured = window_temperature(fc, window, now, "high", day)
        if measured.value is None:
            return {**slots, "error": "no_data"}
        return {**slots, "value": _round(measured.value)}

    if measure == "low":
        low_day = day + timedelta(days=1) if day_label == "tonight" else day
        window = night_low_window(low_day)
        if window.end <= now:
            return {**slots, "error": "passed"}
        measured = window_temperature(fc, window, now, "low", low_day)
        if measured.value is None:
            return {**slots, "error": "no_data"}
        return {**slots, "value": _round(measured.value)}

    # Outlook: conditions, high and low, and rain for the day, or the night for "tonight"
    tonight = day_label == "tonight"
    window = part_window(day, "night") if tonight else part_window(day, "day")
    if window.end <= now:
        return {**slots, "error": "passed"}

    chance = window_chance(fc, window, now, window.start.date())
    if chance.seen == 0:
        return {**slots, "error": "no_data"}

    high = None
    if not tonight:
        measured_high = window_temperature(fc, window, now, "high", day)
        high = _round(measured_high.value)

    low_day = day + timedelta(days=1) if tonight else day
    measured_low = window_temperature(
        fc, night_low_window(low_day), now, "low", low_day
    )

    return {
        **slots,
        "condition": dominant_condition(chance.conditions),
        "high": high,
        "low": _round(measured_low.value),
        "rain_band": chance.band,
        "rain_word": precip_word(chance.conditions, None),
        "rain_chance": _round(chance.percent),
    }


def rain_slots(
    fc: Forecasts,
    day_label: str,
    day: date | None,
    part: str | None,
    asked: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Return speech slots for a rain or snow question about a day or part of a day."""
    slots: dict[str, Any] = {
        **error_slots(None),
        "day": day_label,
        "part": part,
        "rain_band": "none",
        "rain_word": asked or "rain",
        "rain_chance": None,
    }
    if day is None or not fc.has_any():
        return {**slots, "error": "no_data"}

    window = part_window(day, part) if part else part_window(day, "all")
    if window.end <= now:
        return {**slots, "error": "passed"}

    chance = window_chance(fc, window, now, window.start.date())
    if chance.seen == 0:
        return {**slots, "error": "no_data"}

    word = precip_word(chance.conditions, asked)
    band = chance.band
    percent = chance.percent
    # Asked about snow with no snow forecast: say so, and do not quote a rain chance
    if asked == "snow" and not set(chance.conditions) & SNOW_CONDITIONS:
        band = "none"
        percent = None

    return {
        **slots,
        "rain_band": band,
        "rain_word": word,
        "rain_chance": _round(percent),
    }
