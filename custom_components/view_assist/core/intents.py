"""Manage VA intents."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import importlib
import inspect
from inspect import signature
import logging
from pathlib import Path
import pkgutil
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import dispatcher
from homeassistant.helpers.intent import (
    DATA_KEY as INTENT_DATA_KEY,
    Intent,
    IntentHandler,
    IntentResponse,
    async_register,
    async_remove,
)
from homeassistant.helpers.start import async_at_started

from ..const import (  # noqa: TID252
    DOMAIN,
    ENABLE_INTENT_OVERRIDES,
    INSTALL_CUSTOM_SENTENCES,
)
from ..helpers import (  # noqa: TID252
    get_entity_attribute,
    get_entity_id_from_conversation_device_id,
)
from ..typed import VAConfigEntry  # noqa: TID252
from . import intent_handlers

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class IntentHandlerInfo:
    """Definition of override handler for intent."""

    intent: str
    handler: Callable[[Any, Any], Awaitable[IntentResponse | None]]
    call_original: bool = False


def discover_intent_handler_classes() -> dict[str, type[IntentHandler]]:
    """Discover IntentHandler subclasses defined in the intent_overrides package."""
    handler_classes: dict[str, type[IntentHandler]] = {}
    for module_info in pkgutil.iter_modules(
        intent_handlers.__path__, f"{intent_handlers.__name__}."
    ):
        module = importlib.import_module(module_info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, IntentHandler)
                and obj is not IntentHandler
                and obj.__module__ == module.__name__
            ):
                handler_classes[obj().intent_type] = obj
    return handler_classes


def discover_sentence_files(path: Path) -> list[Path]:
    """Discover sentence files in the given path."""
    return list(path.glob("*.yaml")) if path.is_dir() else []


def intent_to_dict(intent_obj: Intent) -> dict[str, Any]:
    """Convert the intent object to a dictionary."""
    return {
        "platform": intent_obj.platform,
        "intent_type": intent_obj.intent_type,
        "slots": intent_obj.slots,
        "text_input": intent_obj.text_input,
        "context": intent_obj.context.as_dict(),
        "language": intent_obj.language,
        "assistant": intent_obj.assistant,
        "device_id": intent_obj.device_id,
        "satellite_id": intent_obj.satellite_id,
        "conversation_agent_id": intent_obj.conversation_agent_id,
    }


def intent_response_to_dict(response: IntentResponse) -> dict[str, Any]:
    """Convert the intent response object to a dictionary."""
    return {
        "language": response.language,
        "intent": response.intent,
        "speech": response.speech,
        "reprompt": response.reprompt,
        "card": response.card,
        "error_code": response.error_code,
        "success_results": response.success_results,
        "failed_results": response.failed_results,
        "matched_states": response.matched_states,
        "unmatched_states": response.unmatched_states,
        "speech_slots": response.speech_slots,
        "response_type": response.response_type,
    }


class IntentsManager:
    """Manage VA intents."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> IntentsManager | None:
        """Get the intents manager for a config entry."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.overrides: dict[str, IntentHandlerInfo] = {}

    async def async_setup(self) -> bool:
        """Set up the Intents Manager."""

        if ENABLE_INTENT_OVERRIDES:
            # Discover and register intent handlers, then hook into all
            # registered intents, after Home Assistant has started and
            # hopefully all integrations have registered their intents
            async_at_started(self.hass, self.async_register_intent_hooks_and_handlers)

        # Enable custom sentences for intents
        if INSTALL_CUSTOM_SENTENCES:
            await self.register_custom_sentences()
        else:
            _LOGGER.debug(
                "Custom sentences for intents are disabled. Set INSTALL_CUSTOM_SENTENCES to True to enable"
            )
            await self.unregister_custom_sentences()

        return True

    async def async_unload(self) -> bool:
        """Unload the Intents Manager."""
        self.unregister_intent_hooks_and_handlers()
        await self.unregister_custom_sentences()

        return True

    async def async_register_intent_hooks_and_handlers(self, *args) -> None:
        """Register intent hooks, overriding or adding new from intent_handlers."""
        existing_intents = self.hass.data.get(INTENT_DATA_KEY, {}).copy()
        custom_handlers = await self.hass.async_add_executor_job(
            discover_intent_handler_classes
        )

        # Register hooks with overrides
        for intent_type, handler in existing_intents.items():
            if intent_type in custom_handlers:
                new_handler = IntentHookHandler(
                    handler=custom_handlers[intent_type](),
                    original_handler=handler,
                )
            else:
                new_handler = IntentHookHandler(
                    handler=handler,
                )
            async_remove(self.hass, intent_type)
            async_register(
                self.hass,
                new_handler,
            )
            _LOGGER.debug("Registered intent hook: %s -> %s", intent_type, handler)

        # Register new intents that are not already in existing_intents
        for intent_type, handler_cls in custom_handlers.items():
            if intent_type not in existing_intents:
                new_handler = IntentHookHandler(
                    handler=handler_cls(),
                )
                async_register(
                    self.hass,
                    new_handler,
                )
                _LOGGER.debug(
                    "Registered new intent: %s -> %s", intent_type, new_handler
                )

    def unregister_intent_hooks_and_handlers(self):
        """Unregister intent hooks and restore original handlers."""
        all_intents = self.hass.data.get(INTENT_DATA_KEY, {}).copy()
        for intent_type, handler in all_intents.items():
            if isinstance(handler, IntentHookHandler):
                if handler.original_handler:
                    _LOGGER.debug(
                        "Restoring original intent handler for: %s", intent_type
                    )
                    async_remove(self.hass, intent_type)
                    async_register(self.hass, handler.original_handler)
                else:
                    _LOGGER.debug("Removing intent handler for: %s", intent_type)
                    async_remove(self.hass, intent_type)
                    async_register(self.hass, handler.handler)

    def get_supported_language_id(self, path: Path, language: str) -> str:
        """Get the supported language id for a given language."""
        language_dir = Path(path, language)
        if language_dir.exists():
            return language

        # If language is hyphenated, try the first part (e.g., "en-US" -> "en")
        if "-" in language:
            language_dir = Path(path, language.split("-", 1)[0])
            if language_dir.exists():
                return language.split("-", 1)[0]
        return "en"

    async def register_custom_sentences(self) -> None:
        """Register custom sentences for intents."""
        va_custom_sentence_path = self.hass.config.path(
            f"custom_components/{DOMAIN}/core/custom_sentences"
        )
        language = self.get_supported_language_id(
            va_custom_sentence_path, self.hass.config.language
        )

        # Add symlinks for custom sentences to the HA custom sentences directory
        custom_sentences_dir = Path(self.hass.config.path("custom_sentences", language))
        if not custom_sentences_dir.exists():
            custom_sentences_dir.mkdir(parents=True, exist_ok=True)

        # Register every sentence file found for the supported language
        va_custom_sentences_dir = Path(va_custom_sentence_path, language)
        for src in await self.hass.async_add_executor_job(
            discover_sentence_files, va_custom_sentences_dir
        ):
            dest = custom_sentences_dir / src.name
            if not dest.exists():
                dest.symlink_to(src)

    async def unregister_custom_sentences(self) -> None:
        """Unregister custom sentences for intents."""
        va_custom_sentence_path = self.hass.config.path(
            f"custom_components/{DOMAIN}/core/custom_sentences"
        )
        language = self.get_supported_language_id(
            va_custom_sentence_path, self.hass.config.language
        )

        custom_sentences_dir = Path(self.hass.config.path("custom_sentences", language))
        if not custom_sentences_dir.exists():
            return

        # Remove the symlinks for every sentence file found for the supported language
        va_custom_sentences_dir = Path(va_custom_sentence_path, language)
        for src in await self.hass.async_add_executor_job(
            discover_sentence_files, va_custom_sentences_dir
        ):
            dest = custom_sentences_dir / src.name
            if dest.exists() and dest.is_symlink():
                dest.unlink()


class IntentHookHandler(IntentHandler):
    """Base class for intent handlers with custom hooks."""

    def __init__(
        self,
        handler: IntentHandler,
        original_handler: IntentHandler | None = None,
        call_original: bool = False,
    ) -> None:
        """Initialize the intent handler."""

        self.handler = handler
        self.original_handler = original_handler
        self.call_original = call_original

        self.intent_type = handler.intent_type
        self.platforms = handler.platforms
        self.description = (
            handler.description or f"Hooked handler for {handler.intent_type}"
        )

    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Handle the intent with custom logic."""
        _LOGGER.debug(
            "%s invoked with intent: %s",
            self.__class__.__name__,
            intent_to_dict(intent_obj),
        )

        response = await self.call_handler(self.handler, intent_obj)

        if self.call_original and self.original_handler:
            response = await self.call_handler(self.original_handler, intent_obj)

        _LOGGER.debug(
            "%s response: %s",
            self.__class__.__name__,
            intent_response_to_dict(response),
        )

        # Notify device of intent handling
        dispatcher.async_dispatcher_send(
            intent_obj.hass,
            f"{intent_obj.device_id}-intent_handled",
            intent_obj,
            response,
        )

        return response

    async def call_handler(
        self, handler: IntentHandler, intent_obj: Intent
    ) -> IntentResponse:
        """Call the given intent handler with the provided intent object."""
        # check if handler has an extra_data parameter in async_handle
        sig = signature(handler.async_handle)
        if "extra_data" in sig.parameters:
            return await handler.async_handle(
                intent_obj=intent_obj,
                extra_data=DeviceInfoData(intent_obj).device_info,
            )
        return await handler.async_handle(intent_obj=intent_obj)


class DeviceInfoData:
    """Class to hold device information."""

    def __init__(self, intent_obj: Intent) -> None:
        """Initialize the DeviceInfo class."""
        self.hass = intent_obj.hass
        self.conversation_device_id = intent_obj.device_id

    @property
    def entity_id(self) -> str | None:
        """Get the entity id for the device."""
        return get_entity_id_from_conversation_device_id(
            self.hass, self.conversation_device_id
        )

    @property
    def entity_name(self) -> str | None:
        """Get the entity name for the device."""
        return (
            get_entity_attribute(self.hass, self.entity_id, "friendly_name")
            if self.entity_id
            else None
        )

    @property
    def music_player(self) -> str | None:
        """Get the media player entity id for the device."""
        return (
            get_entity_attribute(self.hass, self.entity_id, "musicplayer_device")
            if self.entity_id
            else None
        )

    @property
    def device_info(self) -> dict[str, Any]:
        """Get device information as a dictionary."""
        return {
            "name": self.entity_name,
            "music_player": self.music_player,
            "entity_id": self.entity_id,
        }
