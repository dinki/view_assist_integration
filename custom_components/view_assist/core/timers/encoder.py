"""Encoder for text to TimerInfo.

Converts a sentence in English to a TimerInfo object.

Order of encoding
1. Convert words to standard words using encoder language pack entries
2. Remove any unwanted words as defined by remove_words in encoder language pack
3. Convert any text numbers to digits (e.g. "two" to "2")
4. Look for time/interval patterns as defined in the language pack structures
5. Look for standard duration patterns (e.g. 1 day 2 hours 30 minutes)
"""

from dataclasses import dataclass
import enum
import json
import logging
from pathlib import Path
import re
import traceback
from typing import Any
import zoneinfo

from homeassistant.components.conversation import (
    ConversationInput,
    async_converse,
    get_agent_manager,
    async_get_agent,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from ...helpers import get_config_entry_by_entity_id, get_key  # noqa: TID252
from ...typed import VATimeFormat  # noqa: TID252
from .. import VAConfigEntry  # noqa: TID252
from ..translator import DOMAIN  # noqa: TID252
from ..translator.translator import LangPackKeys  # noqa: TID252
from ..translator.wordstonumbers import WordsToDigits  # noqa: TID252
from .helpers import PRE_EXPIRE_WARNING, TimerHelpers
from .typed import Duration, TimerInfo

_LOGGER = logging.getLogger(__name__)

ENCODER_PACK = "encoder"


class EncoderPackKeys(enum.StrEnum):
    """Keys for sentence encoder language pack."""

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


# TODO: Build regex patterns from encoder language pack
class RegexPatterns:
    """Regex time patterns for matching."""

    STDTIME = r"(?P<hours>\d{1,2})(?::|\.|h|\s)?(?P<minutes>\d{1,2})?"
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
    "{std_time} hours {day}",
]

PATTERN_TYPE_HINTS = {
    "basic_time": "time",
    "advanced_time": "time",
    "basic_interval": "interval",
    "advanced_interval": "interval",
}


@dataclass
class LLMTimerInfo:
    """Class to hold the output from the LLM."""

    type: str | None = None
    days: int | None = None
    hours: int | None = None
    minutes: int | None = None
    seconds: int | None = None
    datetime: str | None = None


class LLMSentenceEncoder:
    """Class to encode a sentence into a TimerInfo object using LLM."""

    INSTRUCTIONS = """Convert the text '{}' into a time or interval.
    In all cases provide a type slot with the value of either time or interval.
    For a type of interval, provide slots for each of days, hours, minutes and seconds as integers.
    For a type of time, provide a slot called datetime with the value as a datetime in iso format with the current timezone.
    If the datetime is in the past, return the next future occurrence of the requested text.
    Do not return any other information, only return the defined slots as a json formatted string in lowercase.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        source: str | None = None,
        locale: str = "en",
        debug: bool = False,
    ) -> None:
        """Initialise the encoder."""
        self.hass = hass
        self.source = source
        self.locale = locale
        self.debug = debug
        self.agent_id = None

    async def encode(self, string: str) -> TimerInfo:
        """Encode a time/interval string to TimerInfo using LLM."""
        return await self._agent_encoding(string, self.locale)

    async def _agent_encoding(self, sentence: str, locale: str) -> TimerInfo:
        """Encode text using the conversation agent."""

        if agent := async_get_agent(self.hass, agent_id=self.source):
            _LOGGER.warning("Agent: %s", agent)

            instructions = self.INSTRUCTIONS.format(sentence)
            response = await agent.async_process(
                ConversationInput(
                    text=instructions,
                    context=Context(),
                    conversation_id="ViewAssistEncoder",
                    device_id=None,
                    satellite_id=None,
                    language=locale,
                    agent_id=self.source,
                )
            )
            _LOGGER.warning("Result: %s", response.as_dict())

            if result := get_key("response.speech.plain.speech", response.as_dict()):
                try:
                    llm_timer_info = LLMTimerInfo(**json.loads(result))

                    if llm_timer_info.type == "time" and llm_timer_info.datetime:
                        timer_dt = dt_util.parse_datetime(llm_timer_info.datetime)
                        if timer_dt is None:
                            _LOGGER.error(
                                "Invalid datetime returned from LLM: %s",
                                llm_timer_info.datetime,
                            )
                            return None
                        if timer_dt.tzinfo is None:
                            timer_dt = dt_util.as_local(timer_dt)
                        timer_info = TimerHelpers.build_timer_info_from_datetime(
                            timer_dt,
                            timezone=self.hass.config.time_zone,
                        )
                        timer_info.request_sentence = sentence
                        return timer_info
                    if llm_timer_info.type == "interval":
                        timer_info = TimerHelpers.build_timer_info_from_duration(
                            Duration(
                                days=llm_timer_info.days or 0,
                                hours=llm_timer_info.hours or 0,
                                minutes=llm_timer_info.minutes or 0,
                                seconds=llm_timer_info.seconds or 0,
                            )
                        )
                        timer_info.request_sentence = sentence

                        timer_info.tz = self.hass.config.time_zone

                        expiry_dt = TimerHelpers.get_expiry_from_timerinfo(timer_info)
                        timer_info.expires_at = round(expiry_dt.timestamp())
                        timer_info.original_expires_at = round(expiry_dt.timestamp())
                        timer_info.pre_expire_warning = PRE_EXPIRE_WARNING

                        return timer_info
                except Exception as e:  # noqa: BLE001
                    _LOGGER.error(
                        "Failed to parse LLM response for '%s': %s\n%s",
                        sentence,
                        e,
                        traceback.format_exc(),
                    )
                    return None
            _LOGGER.warning("No output from conversation agent")
        _LOGGER.error("Invalid agent id provided")
        return None


class SentenceEncoder:
    """Class to encode a sentence into a TimerInfo object."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: VAConfigEntry,
        locale: str = "en",
        debug: bool = False,
    ) -> None:
        """Initialise the encoder."""
        self.hass = hass
        self.config = config
        self.locale = locale
        self.encodings: dict[str, Any] = {}
        self.language: dict[str, Any] = {}
        self.debug = debug

    def load_language_pack(self, language: str) -> dict[str, Any]:
        """Load language pack."""
        # Get current path of this file
        p = self.hass.config.path("custom_components", DOMAIN)

        if language != "encoder":
            language = language.split("-", 1)[0]

        file = Path(p, "translations", "timers", f"{language}.json")
        if file.is_file():
            try:
                with file.open("r", encoding="utf-8") as f:
                    try:
                        return json.load(f)
                    except json.JSONDecodeError:
                        _LOGGER.error("Error reading language pack for %s", language)
                        return None
            except OSError:
                _LOGGER.error("Error reading language pack for %s", language)
                return None
        return None

    def inString(
        self, string: str, find: str | list[str] | enum.EnumType
    ) -> str | None:
        """Check if a word or list of words is in a string."""
        if isinstance(find, enum.EnumType):
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
        _LOGGER.debug("Building timer info for: %s with type hint of %s", d, type_hint)
        timer_info = TimerInfo()
        timer_info.request_sentence = sentence or None
        timer_info.pattern = pattern or None

        # Fixed items
        timer_info.dayofweek = d["day"] if d.get("day") else ""
        timer_info.meridiem = d["meridiem"] if d.get("meridiem") else ""
        timer_info.timeofday = d["time_of_day"] if d.get("time_of_day") else ""
        timer_info.special_hour = d["special_hour"] if d.get("special_hour") else ""

        # These can come in as floats, so handle that too.

        timer_info.days, part_day = self.handle_floats(d.get("days", "0"))
        timer_info.hours, part_hour = self.handle_floats(d.get("hours", "0"))
        if part_day:
            timer_info.hours += int(part_day * 24)
        timer_info.minutes, part_min = self.handle_floats(d.get("minutes", "0"))
        if part_hour:
            timer_info.minutes += int(part_hour * 60)
        timer_info.seconds = int(float(d["seconds"])) if d.get("seconds") else 0
        if part_min:
            timer_info.seconds += int(part_min * 60)

        if fraction := d.get("fractions"):
            multiplier = 1
            if fraction == "half":
                multiplier = 0.5
            elif fraction == "quarter":
                multiplier = 0.25
            elif fraction == "threequarter":
                multiplier = 0.75

            if timer_info.minutes > 0:
                timer_info.seconds += int(60 * multiplier)
            if timer_info.hours > 0:
                timer_info.minutes += int(60 * multiplier)
            elif timer_info.days > 0:
                timer_info.hours += int(24 * multiplier)
            elif timer_info.minutes == 0:
                timer_info.minutes += int(60 * multiplier)

        if d.get("operator") in ["before", "minus"]:
            if timer_info.minutes > 0:
                timer_info.minutes = 60 - timer_info.minutes
                timer_info.hours -= 1
            elif timer_info.hours > 0:
                timer_info.hours = 24 - timer_info.hours
                timer_info.days -= 1

        timer_info.is_interval = self._is_interval(timer_info, type_hint)
        timer_info.tz = self.hass.config.time_zone

        expiry_dt = TimerHelpers.get_expiry_from_timerinfo(timer_info)
        timer_info.expires_at = round(expiry_dt.timestamp())
        timer_info.original_expires_at = round(expiry_dt.timestamp())
        timer_info.pre_expire_warning = PRE_EXPIRE_WARNING

        if timer_info.is_interval:
            timer_info.normalised_sentence = TimerHelpers.build_duration_text(
                Duration(
                    days=timer_info.days,
                    hours=timer_info.hours,
                    minutes=timer_info.minutes,
                    seconds=timer_info.seconds,
                ),
                language=self.locale,
            )
        else:
            timer_info.normalised_sentence = TimerHelpers.build_datetime_text(
                expiry_dt,
                language=self.locale,
                h24format=self.config.runtime_data.dashboard.display_settings.time_format
                == VATimeFormat.HOUR_24,
            )

        return timer_info

    def _is_interval(self, b: TimerInfo, type_hint: str | None = None) -> bool:
        """Check if the builder represents an interval."""
        if b.days or b.seconds:
            return True
        if b.dayofweek or b.meridiem or b.special_hour or b.timeofday:
            return False
        if type_hint:
            return type_hint == "interval"
        return False

    def normalise_words(self, string: str) -> str:
        """Normalise words in a string."""
        string = string.lower()
        collections = [
            EncoderPackKeys.DIRECT_TRANSLATIONS,
            EncoderPackKeys.DURATIONS,
            EncoderPackKeys.OPERATORS,
            EncoderPackKeys.MERIDIEM,
            EncoderPackKeys.FRACTIONS,
            EncoderPackKeys.SPECIAL_HOURS,
        ]
        for col in collections:
            for word, values in self.encodings.get(col, {}).items():
                if values:
                    if m := self.inString(string, values):
                        for match in m:
                            string = self.replaceInString(string, match, word)
        return string

    async def encode(self, string: str, type_hint: str | None = None) -> TimerInfo:
        """Encode a time/interval string to TimerInfo."""
        self.encodings = await self.hass.async_add_executor_job(
            self.load_language_pack, "encoder"
        )
        self.language = await self.hass.async_add_executor_job(
            self.load_language_pack, self.locale
        )

        if self.encodings and self.language:
            s = self.normalise_words(string)

            # Remove any unwanted words
            for word in self.encodings.get(EncoderPackKeys.REMOVE_WORDS, []):
                if m := self.inString(s, word):
                    for match in m:
                        s = self.replaceInString(s, match, "")

            # Convert any text words to digits
            if any(n for n in self.language[LangPackKeys.NUMBERS] if n in s):
                s = WordsToDigits.convert(" ".join(s.split()))

            # If basic time structure then ensure in 00:00 format
            for std_time_pattern in STD_TIME_PATTERNS:
                s = " ".join(s.replace("oclock", "").split())
                if m := self.run_regex(std_time_pattern, s):
                    _LOGGER.debug(
                        "Standard time pattern matched to: %s", std_time_pattern
                    )
                    return self.build_timer_info(
                        m,
                        sentence=string,
                        pattern=std_time_pattern,
                        type_hint="time",
                    )

            # Load the language pack structures and evaluate them
            # Advanced may ref basic to create more complex patterns
            structures = self.language.get(EncoderPackKeys.STRUCTURES, {})
            for key, patterns in structures.items():
                for str_pattern in patterns:
                    if "{basic_time}" in str_pattern:
                        # Use std time patterns plus lang pack basic time patterns to replace {basic_time} in the structure pattern
                        basic_time_patterns = STD_TIME_PATTERNS
                        basic_time_patterns.extend(structures.get("basic_time", []))
                        for basic_time_pattern in basic_time_patterns:
                            if m := self.run_regex(
                                str(str_pattern).replace(
                                    "{basic_time}", basic_time_pattern
                                ),
                                s,
                            ):
                                _LOGGER.debug(
                                    "%s language pack %s pattern matched to: %s with basic time pattern: %s",
                                    self.locale,
                                    key,
                                    str_pattern,
                                    basic_time_pattern,
                                )
                                return self.build_timer_info(
                                    m,
                                    sentence=string,
                                    pattern=basic_time_pattern,
                                    type_hint=PATTERN_TYPE_HINTS.get(key, type_hint),
                                )
                        continue
                    if m := self.run_regex(str_pattern, s):
                        _LOGGER.debug(
                            "%s language pack %s pattern matched to: %s",
                            self.locale,
                            key,
                            str_pattern,
                        )
                        return self.build_timer_info(
                            m,
                            sentence=string,
                            pattern=str_pattern,
                            type_hint=PATTERN_TYPE_HINTS.get(key, type_hint),
                        )

            # Look for interval duratons
            duration_pattern = self.make_duration_pattern()
            if m := self.run_regex(duration_pattern, s):
                _LOGGER.debug("Duration pattern matched to: %s", duration_pattern)
                return self.build_timer_info(
                    m, sentence=string, pattern="durations", type_hint="interval"
                )
            _LOGGER.warning(
                "No matching pattern to decode '%s' to a time or interval", s
            )
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
