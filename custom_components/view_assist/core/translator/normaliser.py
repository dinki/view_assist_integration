"""Normaliser for text before decoding.

Converts a sentence in english to a TimerInfo object.

Order of normalisation
1. Convert words to standard words using normaliser language pack entries
2. Remove any unwanted words as defined by remove_words in normaliser language pack
3. Convert any text numbers to digits (e.g. "two" to "2")
4. Look for time/interval patterns as defined in the language pack structures
5. Look for standard duration patterns (e.g. 1 day 2 hours 30 minutes)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import EnumType, StrEnum
import json
import logging
from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant

from . import DOMAIN
from .translator import LangPackKeys
from .wordstonumbers import WordsToDigits

_LOGGER = logging.getLogger(__name__)


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
    is_time: bool = False
    is_interval: bool = False
    sentence: str = ""
    pattern: str = ""


class NormaliserPackKeys(StrEnum):
    """Keys for normaliser language pack."""

    DAYS = "days"
    MERIDIEM = "meridiem"
    DURATIONS = "durations"
    OPERATORS = "operators"
    SPECIAL_HOURS = "special_hours"
    TIME_OF_DAY = "time_of_day"
    FRACTIONS = "fractions"
    DIRECT_TRANSLATIONS = "direct_translations"
    REMOVE_WORDS = "remove_words"
    STRUCTURES = "structures"


# TODO: Build regex patterns from normalisation language pack
class RegexPatterns:
    """Regex time patterns for matching."""

    STDTIME = r"(?P<hours>\d{1,2})(?::|h|\s)?(?P<minutes>\d{1,2})?"
    DAYS = r"(?P<days>\d+)"
    HOURS = r"(?P<hours>\d{1,2})"
    MINUTES = r"(?P<minutes>\d{1,2})"
    FRACTIONS = r"(?P<fractions>half|quarter|threequarter)"
    TIMEOFDAY = r"(?P<time_of_day>am|pm|morning|afternoon|evening|night|tonight)"
    DAY = r"(?P<day>monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow)"
    SPECIAL_HOUR = r"(?P<special_hour>noon|midnight)"
    OPERATOR = r"(?P<operator>and|minus|after|before)"
    JOINER_WORDS = r"(?:on|this|at|,)"


class RegexDurationPatterns:
    """Regex patterns for matching durations."""

    DAYS = r"((?P<days>\d{1,2}(.\d+)?)(?:\s)?(?:days|day|d)\b)?"
    HOURS = r"((?P<hours>\d{1,2}(.\d+)?)(?:\s)?(?:hours|hour|h)\b)?"
    MINUTES = r"((?P<minutes>\d{1,2}(.\d+)?)(?:\s)?(?:minutes|minute|mins|min|m)\b)?"
    SECONDS = r"((?P<seconds>\d{1,2})(?:\s)?(?:seconds|second|secs|sec|s)\b)?"
    JOIN = r"(?:,\s|\sand\s|\s)?"


REGEXLOOKUP = {
    "std_time": RegexPatterns.STDTIME,
    "days": RegexPatterns.DAYS,
    "hours": RegexPatterns.HOURS,
    "minutes": RegexPatterns.MINUTES,
    "fractions": RegexPatterns.FRACTIONS,
    "time_of_day": RegexPatterns.TIMEOFDAY,
    "day": RegexPatterns.DAY,
    "special_hour": RegexPatterns.SPECIAL_HOUR,
    "operator": RegexPatterns.OPERATOR,
    "joiner_words": RegexPatterns.JOINER_WORDS,
}


STD_TIME_PATTERNS = [
    "{special_hour}",
    "{std_time}",
    "{std_time}{time_of_day}",
    "{std_time} {time_of_day}",
    "{std_time} {time_of_day} {day}",
    "{std_time} {day}",
    "{std_time} {day} {time_of_day}",
    "{std_time} {joiner_words} {time_of_day}",
    "{std_time} {joiner_words} {day}",
    "{std_time} {joiner_words} {day} {time_of_day}",
    "{day} {std_time}",
    "{day} {std_time} {time_of_day}",
    "{day} {joiner_words} {std_time}",
    "{day} {joiner_words} {std_time} {time_of_day}",
    "{day} {joiner_words} {time_of_day}",
    "{day} {joiner_words} {special_hour}",
]


class Normaliser:
    """Normaliser class."""

    def __init__(
        self, hass: HomeAssistant, locale: str = "en", debug: bool = False
    ) -> None:
        """Initialise the normaliser."""
        self.hass = hass
        self.locale = locale
        self.normalisations: dict[str, Any] = {}
        self.lang: dict[str, Any] = {}
        self.debug = debug

    def load_language_pack(self, lang: str) -> dict[str, Any]:
        """Load language pack."""
        # Get current path of this file
        p = self.hass.config.path("custom_components", DOMAIN)

        if lang != "normaliser":
            lang = lang.split("-")[0]

        file = Path(p, "translations", "timers", f"{lang}.json")
        if file.is_file():
            try:
                with file.open("r", encoding="utf-8") as f:
                    try:
                        return json.load(f)
                    except json.JSONDecodeError:
                        _LOGGER.error("Error reading language pack for %s", lang)
                        return None
            except OSError:
                _LOGGER.error("Error reading language pack for %s", lang)
                return None
        return None

    def inString(self, string: str, find: str | list[str] | EnumType) -> str | None:
        """Check if a word or list of words is in a string."""
        if isinstance(find, EnumType):
            find = list(find)
        if isinstance(find, list):
            find = "|".join(re.escape(f) for f in find if f)
        pattern = r"(?:^|\b)(" + find + r")(?:,|\b|$)"
        if m := re.findall(pattern, string):
            return m
        return None

    def replaceInString(self, string: str, find: str, replace: str) -> str:
        """Replace a word in a string."""
        pattern = r"(^|\b)(" + find.strip() + r")(,|\W|\b|$)"
        return re.sub(pattern, rf" {replace} ", string)

    def run_regex(self, template: str, string: str) -> Any:
        """Run a regex pattern on a string."""
        pattern = self.make_template_regex_pattern(template)
        if self.debug:
            _LOGGER.debug(
                "Running pattern: %s -> %s on string: %s", template, pattern, string
            )
        try:
            if m := re.match(pattern, string):
                return m.groupdict()
        except re.PatternError:
            pass
        return None

    def handle_floats(self, value: str | None) -> tuple[int, float]:
        """Handle float values in strings."""
        if value is None:
            return 0, 0
        if "." in value:
            parts = value.split(".")
            whole = int(parts[0])
            fraction = float(f"0.{parts[1]}")
            return whole, fraction
        return int(value), 0

    def build_timer_info(
        self,
        d: dict[str, Any],
        sentence: str | None = None,
        pattern: str | None = None,
        type_hint: str | None = None,
    ) -> TimerInfo:
        """Build the output from a regex match dictionary."""
        _LOGGER.debug("Building timer info for: %s", d)
        timer = TimerInfo()
        timer.sentence = sentence if sentence else None
        timer.pattern = pattern if pattern else None

        # Fixed items
        timer.dayofweek = d["day"] if d.get("day") else ""
        timer.meridiem = d["meridiem"] if d.get("meridiem") else ""
        timer.timeofday = d["time_of_day"] if d.get("time_of_day") else ""
        timer.special_hour = d["special_hour"] if d.get("special_hour") else ""

        # These can come in as floats, so handle that too.

        timer.days, part_day = self.handle_floats(d.get("days", "0"))
        timer.hours, part_hour = self.handle_floats(d.get("hours", "0"))
        if part_day:
            timer.hours += int(part_day * 24)
        timer.minutes, part_min = self.handle_floats(d.get("minutes", "0"))
        if part_hour:
            timer.minutes += int(part_hour * 60)
        timer.seconds = int(float(d["seconds"])) if d.get("seconds") else 0
        if part_min:
            timer.seconds += int(part_min * 60)

        if fraction := d.get("fractions"):
            multiplier = 1
            if fraction == "half":
                multiplier = 0.5
            elif fraction == "quarter":
                multiplier = 0.25
            elif fraction == "threequarter":
                multiplier = 0.75

            if timer.minutes > 0:
                timer.seconds += int(60 * multiplier)
            if timer.hours > 0:
                timer.minutes += int(60 * multiplier)
            elif timer.days > 0:
                timer.hours += int(24 * multiplier)
            elif timer.minutes == 0:
                timer.minutes += int(60 * multiplier)

        if d.get("operator") in ["before", "minus"]:
            if timer.minutes > 0:
                timer.minutes = 60 - timer.minutes
                timer.hours -= 1
            elif timer.hours > 0:
                timer.hours = 24 - timer.hours
                timer.days -= 1

        timer.is_time = self._is_time(timer, type_hint)

        return timer

    def _is_time(self, b: TimerInfo, type_hint: str | None = None) -> bool:
        """Check if the builder represents a time."""
        if b.dayofweek or b.meridiem or b.special_hour or b.timeofday:
            return True
        if b.days or b.seconds:
            return False
        if type_hint:
            return type_hint == "time"
        return True

    def normalise_words(self, string: str) -> str:
        """Normalise words in a string."""
        string = string.lower()
        collections = [
            NormaliserPackKeys.DIRECT_TRANSLATIONS,
            NormaliserPackKeys.DURATIONS,
            NormaliserPackKeys.OPERATORS,
            NormaliserPackKeys.MERIDIEM,
            NormaliserPackKeys.FRACTIONS,
            NormaliserPackKeys.SPECIAL_HOURS,
        ]
        for col in collections:
            for word, values in self.normalisations.get(col, {}).items():
                if values:
                    if m := self.inString(string, values):
                        for match in m:
                            string = self.replaceInString(string, match, word)
        return string

    async def normalise(self, string: str, type_hint: str | None = None) -> TimerInfo:
        """Normalise a time/interval string."""
        self.normalisations = await self.hass.async_add_executor_job(
            self.load_language_pack, "normaliser"
        )
        self.lang = await self.hass.async_add_executor_job(
            self.load_language_pack, self.locale
        )

        if self.normalisations and self.lang:
            s = self.normalise_words(string)

            # Remove any unwanted words
            for word in self.normalisations.get(NormaliserPackKeys.REMOVE_WORDS, []):
                if m := self.inString(s, word):
                    for match in m:
                        s = self.replaceInString(s, match, "")

            # Convert any text words to digits
            if any(n for n in self.lang[LangPackKeys.NUMBERS] if n in s):
                s = WordsToDigits.convert(" ".join(s.split()))

            # If basic time structure then ensure in 00:00 format
            for std_time_pattern in STD_TIME_PATTERNS:
                s = " ".join(s.replace("oclock", "").split())
                if m := self.run_regex(std_time_pattern, s):
                    return self.build_timer_info(
                        m,
                        sentence=string,
                        pattern=std_time_pattern,
                        type_hint="time",
                    )

            # Load the language pack structures and evaluate them
            # Advanced may ref basic to create more complex patterns
            structures = self.lang.get(NormaliserPackKeys.STRUCTURES, {})
            for patterns in structures.values():
                for str_pattern in patterns:
                    if "{basic_time}" in str_pattern:
                        basic_time_patterns = structures.get("basic_time", [])
                        for basic_time_pattern in basic_time_patterns:
                            if m := self.run_regex(
                                str(str_pattern).replace(
                                    "{basic_time}", basic_time_pattern
                                ),
                                s,
                            ):
                                return self.build_timer_info(
                                    m,
                                    sentence=string,
                                    pattern=basic_time_pattern,
                                    type_hint=type_hint,
                                )
                        continue
                    if m := self.run_regex(str_pattern, s):
                        return self.build_timer_info(
                            m, sentence=string, pattern=str_pattern, type_hint=type_hint
                        )

            # Look for interval duratons
            duration_pattern = self.make_duration_pattern()
            if m := self.run_regex(duration_pattern, s):
                return self.build_timer_info(
                    m, sentence=string, pattern="durations", type_hint="interval"
                )
            _LOGGER.warning("Unable to decode '%s' to a time or interval", s)
        return None

    def make_template_regex_pattern(self, template: str) -> str:
        """Make a regex pattern from a structure pattern."""
        pattern = template
        # Find all matching {parameters}
        for key, sub in REGEXLOOKUP.items():
            pattern = pattern.replace("{" + key + "}", sub)

        # Optional items are wrapped in []
        optional_items: list[str] = re.findall(r"\[(.*?)\]", pattern)
        for items in optional_items:
            optional = [item.strip() for item in items.strip().split(",")]
            pattern = pattern.replace(
                f"[{items}] ", rf"(?:^|\b)(?:{'|'.join(optional)}\s)?"
            )
        return r"^" + pattern + r"$"

    def make_duration_pattern(self) -> str:
        """Make a regex pattern for durations."""
        days = RegexDurationPatterns.DAYS
        hours = RegexDurationPatterns.HOURS
        minutes = RegexDurationPatterns.MINUTES
        seconds = RegexDurationPatterns.SECONDS
        join = RegexDurationPatterns.JOIN
        return f"^{days}{join}{hours}{join}{minutes}{join}{seconds}$"
