"""Translator module for handling different languages."""

from enum import EnumType, StrEnum
import json
import logging
from os import environ
from pathlib import Path
import re
from typing import Any

from homeassistant.components.conversation import async_converse, get_agent_manager
from homeassistant.core import Context, HomeAssistant

from ...helpers import get_config_entry_by_entity_id, get_key  # noqa: TID252
from . import DOMAIN, VAConfigEntry

_LOGGER = logging.getLogger(__name__)


class LangPackKeys(StrEnum):
    """Keys for language pack entries."""

    NUMBERS = "numbers"
    DAYS = "days"
    DURATIONS = "durations"
    OPERATORS = "operators"
    TIME_OF_DAY = "time_of_day"
    FRACTIONS = "fractions"
    DIRECT_TRANSLATIONS = "direct_translations"
    COMPOUND_WORDS = "compound_words"
    OTHER_WORDS = "other_words"


class LangPackKeys2(StrEnum):
    """Keys for language pack entries."""

    DECIMAL_SEPARATOR = "decimal_separator"


CLEAN_SYMBOLS = ["-", "_"]


PROJECT_ID = environ.get("PROJECT_ID", "")


# TODO: Add ability to use Conversation Engine (LLM) or Translation services like Google, DeepL, LibreTranslate etc.
class ConversationAgentTranslator:
    """Translate text using a conversation agent.

    Basically an LLM translator
    """

    INSTRUCTIONS = """Translate the text in quotation marks from {}.  All numbers should be digits and not words.
    If the output is a time, provide it in the format %A %H:%M if it has a day or %H:%M otherwise.
    If the output is an interval, provide it in days, hours, minutes, seconds.  The text is '{}'"""

    RESPONSE = """Translate the text in quotation marks into a time or interval sentence in a spoken style in the language of locale {}.  The text is '{}'."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise the conversation agent translator."""
        self.hass = hass
        self.agent_id = None
        self.config = config

        self.responses = {
            "timer_set": "Timer set for {time}",
            "timer_named_set": "{name} timer set for {time}",
            "timer_already_exists": "A timer called {name} already exists",
            "timer_none": "No timers are set",
            "timer_list": "You have the following timers set: {timers}",
            "timer_deleted": "Deleted timer {name}",
            "timer_not_found": "No timer called {name} could be found",
            "time_remaining": "{remaining} remaining on timer {name}",
            "timer_error": "Unable to decode time or interval information",
        }

    async def _agent_translation(self, sentence: str, locale: str) -> str:
        """Translate text using the conversation agent."""
        am = get_agent_manager(self.hass)
        agent_entity = self.config.runtime_data.integration.translation_engine
        if agent_config := get_config_entry_by_entity_id(self.hass, agent_entity):
            self.agent_id = agent_config.entry_id

        if am.async_is_valid_agent_id(self.agent_id):
            # TODO: Add device id etc in here
            response = await async_converse(
                self.hass,
                sentence,
                conversation_id="ViewAssistTranslator",
                context=Context(),
                language=locale,
                agent_id=self.agent_id,
            )
            _LOGGER.warning("Response: %s", response.as_dict())
            if output := get_key("response.speech.plain.speech", response.as_dict()):
                return output
            _LOGGER.warning("No output from conversation agent")
        _LOGGER.error("Invalid translation engine provided")
        return None

    async def translate(self, sentence: str, locale: str) -> str:
        """Translate text to the target language using the conversation agent."""
        return await self._agent_translation(
            self.INSTRUCTIONS.format(locale, sentence), locale
        )

    async def translate_response(
        self, sentence_id: str, params: dict[str, Any] | None = None, locale: str = "en"
    ) -> str | None:
        """Translate a response sentence id with optional params."""
        if sentence_id in self.responses:
            sentence = self.responses[sentence_id]

            # set time param to time_{lang} param
            params["time"] = params.get(f"time_{locale}", params.get("time_en"))

            # Replace params in sentence
            if params:
                for k, v in params.items():
                    sentence = sentence.replace(f"{{{k}}}", str(v))

            return await self._agent_translation(
                self.RESPONSE.format(locale, sentence), locale
            )
        return None


class TimeSentenceTranslator:
    """Translate time sentences to english."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise the translator."""
        self.hass = hass
        self.loaded_lang: str | None = None
        self.lang: dict[str, Any] = {}
        self.config = config

    def _two_char_locale(self, lang: str) -> str:
        """Convert locale to two character format."""
        return lang[:2]

    def load_language_pack(self, lang: str) -> bool:
        """Load language pack."""
        # Get current path of this file
        p = self.hass.config.path("custom_components", DOMAIN)
        # In case like de-DE, make de
        lang = self._two_char_locale(lang)
        lang_file = Path(p, "translations", "timers", f"{lang}.json")

        if lang_file.is_file():
            try:
                with lang_file.open("r", encoding="utf-8") as f:
                    self.lang = json.load(f)
                    self.loaded_lang = lang
                    return True
            except json.JSONDecodeError:
                _LOGGER.error("Error reading language pack for %s", lang)
        else:
            _LOGGER.error("No language pack found for %s -> %s", lang, lang_file)
        return False

    def inString(self, string: str, find: str | list[str] | EnumType) -> str | None:
        """Check if any of the find words are in the string."""
        if isinstance(find, EnumType):
            find = list(find)
        if isinstance(find, list):
            find = "|".join(re.escape(f) for f in find if f)
        pattern = r"(?:^|\b)(" + find + r")(?:,|\b|$)"
        if m := re.findall(pattern, string):
            return m
        return None

    def replaceInString(self, string: str, find: str, replace: str) -> str:
        """Replace find word in string with replace word."""
        pattern = r"(^|\b)(" + find.strip() + r")(,|\W|\b|$)"
        return re.sub(pattern, rf" {replace} ", string)

    def clean_sentence(self, s: str) -> str:
        """Preprocess sentence to remove and replace words/text/symbols."""
        s = f" {s.lower().strip()} "

        # Remove unwanted symbols - fix for Goolge STT hyphenating interval times like 5-minutes
        for sym in CLEAN_SYMBOLS:
            s = s.replace(sym, " ")

        # Replace decimal separator with .
        if sep := self.lang.get(LangPackKeys2.DECIMAL_SEPARATOR):
            pattern = rf"(\d+){re.escape(sep)}(\d+)"
            s = re.sub(pattern, r"\1.\2", s)

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

    def _translate_collection(self, string: str, collection_id: LangPackKeys) -> str:
        """Translate all entries in a collection."""
        collection: dict[str, list[str] | str] = self.lang.get(collection_id)
        if not collection:
            return string

        # make into big list of words to match
        collection_words = []
        for entry in collection.values():
            if isinstance(entry, list):
                collection_words.extend(entry)
            else:
                collection_words.append(entry)

        # Order by those with spaces first and then longer words first
        collection_words = sorted(
            collection_words, key=lambda x: (-len(x.split()), -len(x))
        )

        if m := self.inString(string, collection_words):
            for match in m:
                for translation, words in collection.items():
                    if match in words:
                        string = self.replaceInString(string, match, translation)
                        break
        return string

    def _flatten(self, lst: list[str | list]) -> list[str]:
        """Flatten a list of strings and lists into a single list of strings."""
        flattened = []
        for item in lst:
            if isinstance(item, list):
                flattened.extend(self._flatten(item))
            else:
                flattened.append(item)
        return list(filter(None, flattened))

    def _unpack_compound_words(self, string: str) -> str:
        """Unpack compound words in a string."""

        def get_params(fragment: str) -> list[str]:
            params = re.findall(r"\{(.*?)\}", fragment)
            return params if params else []

        compounds: dict[str, str] | None = self.lang.get(LangPackKeys.COMPOUND_WORDS)
        if not compounds:
            return string

        for compound, template in compounds.items():
            if "{" in compound and "}" in compound:
                # It's a template with parameters, build search regex
                params = get_params(compound)
                pattern = re.escape(compound)
                for param in params:
                    if ":" in param:
                        # TODO: Use langpack enum to allow any of these types
                        p_name, p_type = param.split(":", 1)
                        values = ""
                        if p_type == "numbers":
                            values = self._flatten(
                                self.lang.get(LangPackKeys.NUMBERS, {}).values()
                            )

                        elif p_type == "days":
                            values = self._flatten(
                                self.lang.get(LangPackKeys.DAYS, {}).values()
                            )

                        elif p_type == "time_of_day":
                            values = self._flatten(
                                self.lang.get(LangPackKeys.TIME_OF_DAY, {}).values()
                            )

                        if values:
                            pattern = pattern.replace(
                                r"\{" + param + r"\}",
                                r"(?P<" + p_name + r">" + "|".join(values) + r")",
                            )
                    else:
                        pattern = pattern.replace(
                            r"\{" + param + r"\}", r"(?P<" + param + r">\S+)"
                        )
                pattern = r"(?:^|\b)" + pattern + r"(?:\b|$)"

                # Replace matches by group name in template
                if matches := re.finditer(pattern, string):
                    for match in matches:
                        replacement = template
                        for param in params:
                            if ":" in param:
                                param = param.split(":", 1)[0]
                            if param in match.groupdict():
                                replacement = replacement.replace(
                                    "{" + param + "}", match.group(param)
                                )
                        string = re.sub(pattern, f" {replacement} ", string, count=1)
        return string

    async def translate(
        self, sentence: str, locale: str = "en", clean_untranslated: bool = False
    ) -> str:
        """Load translation file and translate sentence."""
        locale = self._two_char_locale(locale)
        if not self.load_language_pack or self.loaded_lang != locale:
            result = await self.hass.async_add_executor_job(
                self.load_language_pack, locale
            )
            if not result:
                return sentence

        # Preprocess sentence to ensure structure
        s = self.clean_sentence(sentence)

        # Perform any direct translations first
        s = self._unpack_compound_words(s)

        # Convert basic numbers
        s = self._translate_collection(s, LangPackKeys.NUMBERS)

        collections = [
            LangPackKeys.TIME_OF_DAY,
            LangPackKeys.DAYS,
            LangPackKeys.FRACTIONS,
            LangPackKeys.DURATIONS,
            LangPackKeys.OPERATORS,
            LangPackKeys.NUMBERS,
            LangPackKeys.OTHER_WORDS,
            LangPackKeys.DIRECT_TRANSLATIONS,
        ]

        _LOGGER.debug("Translating sentence: %s", s)

        for col in collections:
            s = self._translate_collection(s, col)

        if clean_untranslated:
            # Remove any non english words left (i.e. untranslatable words)
            sentence_words = s.split()
            # Build all supported words list
            known_words = []
            for group in LangPackKeys:
                if group_dict := self.lang.get(group):
                    for key in group_dict:
                        known_words.extend(key.split())
            output = []
            for word in sentence_words:
                # if number just add
                try:
                    float(word)
                    output.append(word)
                    continue
                except ValueError:
                    if word[0].isdigit():
                        continue
                    if word in known_words:
                        output.append(word)

            return " ".join(output)
        return " ".join(s.split())

    async def translate_response(
        self, sentence_id: str, params: dict[str, Any] | None = None, locale: str = "en"
    ) -> str | None:
        """Translate a response sentence id with optional params."""
        language = self._two_char_locale(locale)
        if not self.loaded_lang or self.loaded_lang != language:
            result = await self.hass.async_add_executor_job(
                self.load_language_pack, language
            )
            if not result:
                return None

        responses: dict[str, str] | None = self.lang.get("responses")
        if not responses:
            return None

        sentence = responses.get(sentence_id)
        if not sentence:
            return None

        # set time param to time_{lang} param
        params["time"] = params.get(f"time_{locale}", params.get("time_en"))

        # Replace params in sentence
        if params:
            for k, v in params.items():
                sentence = sentence.replace(f"{{{k}}}", str(v))

        _LOGGER.debug("Response is %s with %s -> %s", sentence_id, params, sentence)

        return sentence
