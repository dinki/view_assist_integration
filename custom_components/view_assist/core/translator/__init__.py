"""Init file for translator module."""

from dataclasses import dataclass
from typing import Any

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant

from ...const import DOMAIN  # noqa: TID252
from ...typed import VAConfigEntry  # noqa: TID252
from .translator import ConversationAgentTranslator, TimeSentenceTranslator

__all__ = [
    "DOMAIN",
    "ConversationAgentTranslator",
    "Normaliser",
    "TimeSentenceTranslator",
    "TimerInfo",
]


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


class Translator:
    @classmethod
    def get(cls, hass: HomeAssistant) -> Translator | None:
        """Get the websocket manager for a config entry."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise the translator."""
        self.hass = hass
        self.config = config
        self.translator = None

    async def async_setup(self) -> bool:
        """Set up the Translator."""
        engine = self.config.runtime_data.integration.translation_engine

        if engine is None or engine == conversation.HOME_ASSISTANT_AGENT:
            self.translator = TimeSentenceTranslator(self.hass, self.config)
        else:
            self.translator = ConversationAgentTranslator(self.hass, self.config)

        return True

    async def async_unload(self) -> bool:
        """Unload the Translator."""
        # Currently nothing to unload
        return True

    async def translate_time(self, text: str, locale: str = "en") -> str:
        """Translate the given text."""
        if self.translator is None:
            return text

        return await self.translator.translate(text, locale=locale)

    async def translate_time_response(
        self, sentence_id: str, params: dict[str, Any] | None = None, locale: str = "en"
    ) -> str | None:
        """Translate the given response."""
        if self.translator is None:
            return None

        return await self.translator.translate_response(
            sentence_id, params=params, locale=locale
        )
