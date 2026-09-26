"""Decoder for time-related phrases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import json
import logging
from pathlib import Path
from typing import Any

import wordtodigits

from homeassistant.core import HomeAssistant

from ..const import DOMAIN  # noqa: TID252

_LOGGER = logging.getLogger(__name__)


class Days(StrEnum):
    """Days of the week enum."""

    SUNDAY = "sunday"
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    TODAY = "today"
    TOMORROW = "tomorrow"


class Durations(StrEnum):
    """Duration types enum."""

    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"
    SECOND = "second"


class HourPrefixes(StrEnum):
    """Hour prefixes for time expressions."""

    TO = "to"
    PAST = "past"


class Meridiem(StrEnum):
    """Meridiem indicators."""

    AM = "am"
    PM = "pm"


class SpecialMinutes(StrEnum):
    """Special minute indicators."""

    OCLOCK = "oclock"
    QUARTER = "quarter"
    QUARTERPAST = "quarterpast"
    QUARTERTO = "quarterto"
    HALF = "half"
    HALFPAST = "halfpast"
    THREEQUARTER = "threequarter"


class SpecialMinuteConversion(StrEnum):
    """Special minute conversions for calculating time."""

    OCLOCK = ""
    QUARTER = "15"
    HALF = "30"
    THREEQUARTER = "45"
    DAY_QUARTER = "6"
    DAY_HALF = "12"
    DAY_THREEQUARTER = "18"


@dataclass
class TimerInterval:
    """Data class for timer intervals."""

    sentence: str | None = None
    translated: str | None = None
    day: int = 0
    hour: int = 0
    minute: int = 0
    second: int = 0


@dataclass
class TimerTime:
    """Data class for specific timer times."""

    sentence: str | None = None
    translated: str | None = None
    day: str | None = None
    meridiem: str | None = None
    time: str | None = None


class LangPackKeys(StrEnum):
    """Language pack keys."""

    NUMBERS = "numbers"
    DAYS = "days"
    DURATIONS = "durations"
    HOUR_PREFIXES = "hour_prefixes"
    MERIDIEM = "meridiem"
    SPECIAL_MINUTES = "special_minutes"
    REMOVE_WORDS = "remove_words"
    REPLACE_TEXT = "replace_text"


REMOVE_CHARS = [",", ";", "!", "?", "'", '"']


class SentenceDecoder:
    """Class to decode time and interval sentences."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> SentenceDecoder | None:
        """Get the websocket manager for a config entry."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, lang: str = "en") -> None:
        """Initialise."""
        self.hass = hass
        self.lang = lang
        self.translator: TimeSentenceTranslator | None = None

        self.day: str | None = None
        self.minutes_adjustment: int = 0
        self.meridiem: str | None = None

    async def async_setup(self) -> bool:
        """Set up the Sentence Decoder."""
        if not self.translator:
            self.translator = TimeSentenceTranslator(self.hass, self.lang)
        return True

    async def async_unload(self) -> bool:
        """Unload the Sentence Decoder."""
        return True

    def decode(self, sentence: str) -> TimerTime | TimerInterval:
        """Decode a time expression into a datetime object."""
        if not self.translator:
            self.translator = TimeSentenceTranslator(self.hass, self.lang)

        translated = self.translator.translate()

        if self._is_interval(translated):
            # Decode as interval
            t = TimerInterval(sentence=sentence, translated=translated)
            return self.decode_interval(t)
        # Decode as time
        t = TimerTime(sentence=sentence, translated=translated)
        return self.decode_time(t)

    def decode_interval(self, t: TimerInterval) -> TimerInterval:
        """Decode time intervals like '2 hours 30 minutes'."""

        processed = " ".join(t.translated.split())
        interval = {}
        last_processed_duration = None

        # Get interval parts from duration tags
        remaining_sentence = processed
        carry = ""
        for duration in Durations:
            if carry:
                interval[duration] = carry
                carry = ""

            if m := self.get_match(remaining_sentence, duration):
                parts = remaining_sentence.split(m)
                if len(parts) == 2:
                    # Handle if duration with no value. Assume 1
                    if parts[0].strip() == "":
                        parts[0] = "1"

                    # Handle if special interval in duration
                    part0 = parts[0].strip()
                    if self._is_number(part0):
                        interval[duration] = part0
                    else:
                        for sm in SpecialMinutes:
                            if m := self.get_match(part0, sm):
                                carry = self._convert_special_minute(
                                    duration=duration, special_minute=sm
                                )
                                interval[duration] = part0.replace(m, "").strip()
                                break
                    remaining_sentence = parts[1].strip()
                    last_processed_duration = duration

        # if anything left in remaining sentence, see if it is special time and add to interval below last processed duration
        # ie if last processed duration is hour, add to minutes
        if remaining_sentence:
            if m := self.get_match(remaining_sentence, list(SpecialMinutes)):
                idx = list(Durations).index(last_processed_duration)
                if idx + 1 < len(Durations):
                    next_duration = list(Durations)[idx + 1]
                    interval[next_duration] = self._convert_special_minute(
                        next_duration, m
                    )

        # Set interval values on TimerInterval object
        carry = 0
        for key, value in interval.items():
            try:
                value = float(value)
            except ValueError:
                value = 0

            if value != int(value):
                # If decimal, add remainder to lower duration
                idx = list(Durations).index(key)
                if idx + 1 < len(Durations):
                    lower_duration = list(Durations)[idx + 1]
                    part = value - int(value)
                    setattr(
                        t,
                        lower_duration,
                        int(part * 24) if key == Durations.DAY else int(part * 60),
                    )
            setattr(t, key, int(value) if value else 0)
        return t

    def decode_time(self, t: TimerTime) -> TimerTime:
        """Decode specific time like '4:30 PM' or 'quarter past 3'."""

        processed = t.translated.strip()

        adjustment = 0

        # Extract day if mentioned
        for day in Days:
            if m := self.get_match(processed, day):
                t.day = m
                processed = processed.replace(m, "").strip()
                break

        # Convert word intervals to time adjustments
        for sm in SpecialMinutes:
            if m := self.get_match(processed, sm):
                processed = processed.replace(m, "").strip()
                convert_to = SpecialMinuteConversion[sm.upper()]
                adjustment = int(convert_to) if self._is_number(convert_to) else 0
                break

        # Extract meridiem if mentioned
        for mer in Meridiem:
            if m := self.get_match(processed, mer, whole_word=False):
                t.meridiem = m
                processed = processed.replace(m, "").strip()
                break

        # Convert phrases like "20 past 4" to "4:20"
        for addition in HourPrefixes:
            if m := self.get_match(processed, addition):
                parts = processed.split(m)
                if len(parts) == 2:
                    first_part = parts[0].strip()
                    if self._is_number(first_part):
                        adjustment = (
                            int(first_part)
                            if addition == HourPrefixes.PAST
                            else -int(first_part)
                        )
                    # Adjustment may already have been set by special minutes
                    elif adjustment > 0:
                        if m == HourPrefixes.TO:
                            adjustment = -adjustment

                    processed = parts[1].strip()

        # Special handling for "half [hour]" with no duration marker
        parts = processed.split(" ")
        if (
            len(parts) == 2
            and parts[0] == SpecialMinuteConversion.HALF
            and self._is_number(parts[1])
        ):
            adjustment = 30
            processed = parts[1].strip()

        # Ensure correct spacing
        processed = " ".join(processed.split())

        # Convert number to time ie 1600 to 16:00
        if self._is_number(processed) and len(processed) in [3, 4]:
            if len(processed) == 3:
                processed = f"{processed[0]}:{processed[1:]}"
            else:
                processed = f"{processed[:2]}:{processed[2:]}"

        # If just hour with no minutes, add :00
        if self._is_number(processed) and len(processed) in [1, 2]:
            processed = f"{processed}:00"

        # Set final time field
        try:
            hours, minutes = processed.split(":")
            hours = int(hours)
            if hours < 12 and t.meridiem == Meridiem.PM:
                hours += 12

            tm = datetime.now().replace(
                hour=hours, minute=int(minutes), second=0, microsecond=0
            )
            if adjustment != 0:
                tm = tm + timedelta(minutes=adjustment)
            t.time = tm.strftime("%H:%M")
        except ValueError:
            pass

        return t

    def _is_interval(self, s: str) -> bool:
        durations = Durations
        return any(self.get_match(s, duration) for duration in durations)

    def _is_number(self, s: str | None = None) -> bool:
        """Check if string is a number. Including decimals."""
        if s is None or s == "":
            return False

        allowed_chars = "0123456789."
        return all(char in allowed_chars for char in s)

    def get_match(
        self, s: str, options: str | list[str], whole_word: bool = True
    ) -> str | None:
        """Get first matching option in string."""
        if isinstance(options, str):
            options = [options]

        s = f" {s.strip()} "

        for option in options:
            if whole_word and f" {option} " in s:
                return option
            if not whole_word and option in s:
                return option
        return None

    def _convert_special_minute(
        self, duration: Durations, special_minute: SpecialMinutes
    ) -> str | None:
        """Convert special minute like 'half' or 'quarter' to numeric value."""

        if duration == Durations.DAY:
            key = f"DAY_{special_minute.name}"
        else:
            key = special_minute.name

        try:
            return SpecialMinuteConversion[key]
        except KeyError:
            return None


class TimeSentenceTranslator:
    """Translate time sentences to english."""

    def __init__(self, hass: HomeAssistant, locale: str = "en") -> None:
        """Initialise."""
        self.hass = hass
        self.locale = locale
        self.lang: dict[str, Any] = {}

    def load_language_pack(self, lang: str) -> None:
        """Load language pack."""
        # Get current path of this file
        p = Path(
            self.hass.config.path(DOMAIN), "translations", "timers", f"{lang}.json"
        )

        if not p.exists():
            p = Path(Path(__file__).parent, "translations", "timers", "en.json")
        try:
            with p.open(mode="r", encoding="utf-8") as f:
                self.lang = json.load(f)
        except json.JSONDecodeError:
            _LOGGER.error("Error decoding language file %s", p)
        except OSError:
            _LOGGER.error("Error loading language file %s", p)

    def get_match(self, s: str, options: str | list[str]) -> str | None:
        """Get first matching option in string."""
        if isinstance(options, str):
            options = [options]

        s = f" {s.strip()} "

        for option in options:
            if f" {option} " in s:
                return option
        return None

    def clean_sentence(self, s: str) -> str:
        """Clean sentence by removing unwanted characters and words."""
        s = f" {s.strip()} "

        # Replace decimal separator with .
        if self.lang.get("decimal_separator"):
            s = s.replace(self.lang["decimal_separator"], ".")

        # Replace text
        if rt := self.lang.get(LangPackKeys.REPLACE_TEXT):
            for old, new in rt.items():
                s = s.replace(old, new)

        # Remove unwanted characters
        for char in REMOVE_CHARS:
            s = s.replace(char, "")

        # Remove unwanted words
        if rw := self.lang.get(LangPackKeys.REMOVE_WORDS):
            for word in rw:
                s = s.replace(f" {word} ", " ")

        # Ensure 1 space between words
        return " ".join(s.split())

    def _order_lang_key_entries(self, lang_key: str) -> dict[str, Any]:
        """Order entries in lang_key by length of entry, longest first."""
        if lang_key not in self.lang:
            return {}

        sorted_keys = sorted(self.lang.get(lang_key), key=len, reverse=True)
        return dict(
            zip(
                sorted_keys,
                [self.lang.get(lang_key)[key] for key in sorted_keys],
                strict=False,
            )
        )

    def translate(self, sentence: str) -> str:
        """Load translation file and translate sentence."""
        if not self.lang:
            self.load_language_pack(self.locale)

        # Make sentence lowercase for matching
        s = sentence.lower()

        # Preprocess sentence to remove and replace words/text/symbols
        s = self.clean_sentence(s)

        # Handle special cases like "quarter", "half", "oclock"
        # Important this is first to stop three quarters being translated to 3 quarters
        if sm := self._order_lang_key_entries(LangPackKeys.SPECIAL_MINUTES):
            for sm, words in sm.items():
                if m := self.get_match(s, words):
                    s = s.replace(m, sm)

        # Replace variants with standard am/pm
        if mr := self._order_lang_key_entries(LangPackKeys.MERIDIEM):
            for mr, variants in mr.items():
                if m := self.get_match(s, variants):
                    s = s.replace(m, mr)

        # Replace days of the week
        if dow := self._order_lang_key_entries(LangPackKeys.DAYS):
            for day, variants in dow.items():
                if m := self.get_match(s, variants):
                    s = s.replace(m, day)  # Remove day from time string

        # Replace language numbers with digits
        if num := self._order_lang_key_entries(LangPackKeys.NUMBERS):
            for digit, variants in num.items():
                if m := self.get_match(s, variants):
                    s = s.replace(m, str(digit))

        # Replace duration words with standard duration
        if dur := self._order_lang_key_entries(LangPackKeys.DURATIONS):
            for duration, variants in dur.items():
                if m := self.get_match(s, variants):
                    s = s.replace(m, duration)

        # Replace any special additions like "past" or "to"
        if hour_prefixes := self._order_lang_key_entries(LangPackKeys.HOUR_PREFIXES):
            for addition, variants in hour_prefixes.items():
                if m := self.get_match(s, variants):
                    s = s.replace(m, addition)

        # Finally convert any text words to digits
        if any(n for n in self.lang[LangPackKeys.NUMBERS] if n in s):
            s = wordtodigits.convert(s)
        return " ".join(s.split())
