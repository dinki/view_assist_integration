"""Class to handle timers with persistent storage."""

from datetime import datetime
import logging
import time
from typing import Any
import zoneinfo

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_NAME, ATTR_TIME
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util, ulid as ulid_util

from ...const import (  # noqa: TID252
    ATTR_EXTRA,
    ATTR_INCLUDE_EXPIRED,
    ATTR_LANGUAGE,
    ATTR_REMOVE_ALL,
    ATTR_TIMER_ID,
    ATTR_TYPE,
    DOMAIN,
    USE_LLM_FOR_TIMER_ENHANCEMENT,
)
from ...helpers import (  # noqa: TID252
    get_device_id_from_entity_id,
    get_entity_id_from_conversation_device_id,
)
from ...typed import VATimeFormat  # noqa: TID252
from ..translator import Translator  # noqa: TID252  # noqa: TID252
from .encoder import LLMSentenceEncoder, SentenceEncoder  # noqa: RUF100, TID252
from .handler import TimerHandler
from .helpers import TimerHelpers, get_named_day
from .typed import (
    Duration,
    Timer,
    TimerClass,
    TimerEvent,
    TimerRemainingInfo,
    TimerInfo,
    TimerStatus,
    TimerType,
)

__all__ = [
    "Duration",
    "SentenceEncoder",
    "Timer",
    "TimerClass",
    "TimerEvent",
    "TimerRemainingInfo",
    "TimerHelpers",
    "TimerInfo",
    "TimerManager",
    "TimerStatus",
    "TimerType",
]


_LOGGER = logging.getLogger(__name__)


class TimerManager:
    """Class to manage timers."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> TimerManager | None:
        """Get the timer manager instance."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: ConfigEntry) -> None:
        """Initialise."""
        self._hass = hass
        self._config = config
        self._handler = TimerHandler(hass, config)
        self._tz: zoneinfo.ZoneInfo = zoneinfo.ZoneInfo(hass.config.time_zone)

    async def async_setup(self) -> bool:
        """Set up the Timer Manager."""

        # Register services
        TimerManagerServices(self._hass).register()

        # Start handler
        await self._handler.start()

        return True

    async def async_unload(self) -> bool:
        """Unload Timer Manager."""

        # Stop handler
        await self._handler.stop()

        # Unregister services
        TimerManagerServices(self._hass).unregister()

        return True

    async def add_timer(
        self,
        timer: Timer | None,
        start: bool = True,
    ) -> tuple:
        """Add a new timer."""
        return await self._handler.add_timer(
            timer=timer,
            start=start,
        )

    async def cancel_timer(self, *args, **kwargs):
        """Cancel an existing timer."""
        return await self._handler.cancel_timer(*args, **kwargs)

    async def cancel_sounding_timers(self, *args, **kwargs):
        """Cancel any timers currently sounding for a View Assist entity."""
        return await self._handler.cancel_sounding_timers(*args, **kwargs)

    async def snooze_timer(self, *args, **kwargs):
        """Snooze an existing timer."""
        return await self._handler.snooze_timer(*args, **kwargs)

    def get_timers(self, *args, **kwargs):
        """Get all timers."""
        return self._handler.get_timers(*args, **kwargs)

    def get_timers_as_dict(self, *args, **kwargs):
        """Get all timers as a list of dictionaries."""
        return [timer.to_dict() for timer in self._handler.get_timers(*args, **kwargs)]

    def search_timers(
        self,
        device_id: str = "",
        entity_id: str = "",
        timer_id: str = "",
        timer_class: str = "",
        name: str = "",
        expires_at: int | None = None,
        duration: Duration | None = None,
        include_expired: bool = False,
        sort: bool = True,
    ) -> list[Timer] | None:
        """Search for timers and return a list of matching timers."""
        return self._handler.search_timers(
            device_id=device_id,
            entity_id=entity_id,
            timer_id=timer_id,
            timer_class=timer_class,
            name=name,
            expires_at=expires_at,
            duration=duration,
            include_expired=include_expired,
            sort=sort,
        )

    async def build_timer(
        self,
        source: str,
        device_id: str,
        entity_id: str | None = None,
        timer_class: str = TimerClass.TIMER,
        name: str | None = None,
        timer_value: str | Duration | datetime | TimerInfo | None = None,
        language: str = "en",
        extra_info: dict[str, Any] | None = None,
    ) -> Timer | None:
        """Build a Timer object from a string, Duration, datetime or TimerInfo."""

        timezone = self._hass.config.time_zone

        # Ensure either device_id or entity_id is provided and ensure device_id is set
        if not device_id and entity_id:
            device_id = get_device_id_from_entity_id(self._hass, entity_id)

        timer_info = None
        if isinstance(timer_value, str):
            _LOGGER.debug(
                "Building %s from string: %s, language: %s",
                timer_class,
                timer_value,
                language,
            )
            timer_info = await self.build_timer_info_from_sentence(
                timer_value, source=source, language=language
            )
        elif isinstance(timer_value, Duration):
            _LOGGER.debug("Building %s from Duration: %s", timer_class, timer_value)
            timer_info = TimerHelpers.build_timer_info_from_duration(
                timer_value, timezone=timezone, language=language
            )
        elif isinstance(timer_value, datetime):
            _LOGGER.debug("Building %s from datetime: %s", timer_class, timer_value)
            timer_info = TimerHelpers.build_timer_info_from_datetime(
                timer_value, timezone=timezone, language=language
            )
        elif isinstance(timer_value, TimerInfo):
            _LOGGER.debug("Building %s from TimerInfo: %s", timer_class, timer_value)
            timer_info = timer_value

        if timer_info and not all(
            [
                timer_info.days == 0,
                timer_info.hours == 0,
                timer_info.minutes == 0,
                timer_info.seconds == 0,
            ]
        ):
            _LOGGER.debug("Building Timer with TimerInfo: %s", timer_info.to_dict())
            return self._build_timer_from_timerinfo(
                source=source,
                device_id=device_id,
                timer_class=timer_class,
                name=name,
                timer_info=timer_info,
                language=language,
                extra_info=extra_info,
            )
        return None

    def _build_timer_from_timerinfo(
        self,
        source: str,
        device_id: str,
        timer_class: str,
        name: str,
        timer_info: TimerInfo,
        language: str = "en",
        extra_info: dict[str, Any] | None = None,
    ) -> Timer | None:
        """Build a Timer object from a TimerInfo object."""
        entity_id = get_entity_id_from_conversation_device_id(self._hass, device_id)
        if not entity_id:
            _LOGGER.warning(
                "No entity_id found for device_id: %s. Cannot build timer", device_id
            )
            return None

        time_now_unix = round(dt_util.now().timestamp())

        # Amend timer class if timer with time or alarm with interval
        if timer_class == TimerClass.TIMER and not timer_info.is_interval:
            timer_class = TimerClass.ALARM
        if timer_class == TimerClass.ALARM and timer_info.is_interval:
            timer_class = TimerClass.TIMER

        # Sometimes we have to add a day because time is in the past. If this happens, the expiry date will be tomorrow.
        # Ensure the dayofweek is set correctly in the timer_info.  If not, set it to the correct day of week.
        if timer_info and timer_info.expires_at and not timer_info.dayofweek:
            expiry_dt = TimerHelpers.get_datetime_from_timestamp(
                timer_info.expires_at, timer_info.tz or self._tz
            )
            timer_info.dayofweek = get_named_day(expiry_dt, language=language)

        # Ensure timer_info.time is set if a time type timer.
        if (
            timer_info
            and timer_info.expires_at
            and not timer_info.is_interval
            and not timer_info.time
        ):
            timer_info.time = TimerHelpers.get_datetime_from_timestamp(
                timer_info.expires_at, timer_info.tz or self._tz
            ).strftime("%H:%M")

        timer = Timer(
            id=ulid_util.ulid_now(),
            timer_class=timer_class,
            timer_type="interval" if timer_info.is_interval else "time",
            source=source,
            name=name,
            entity_id=entity_id,
            conversation_device_id=device_id,
            status=TimerStatus.INACTIVE,
            created_at=time_now_unix,
            created_at_monotonic=time.monotonic_ns(),
            updated_at=time_now_unix,
            timer_info=timer_info,
            extra_info=extra_info,
        )

        _LOGGER.debug("Built timer: %s", timer.to_dict())
        timer.remaining_info = TimerHelpers.build_remaining_info_from_timer(
            timer=timer,
            language=language,
            h24format=(
                self._config.runtime_data.dashboard.display_settings.time_format
                == VATimeFormat.HOUR_24
            ),
        )
        return timer

    async def build_timer_info_from_sentence(
        self,
        sentence: str,
        source: str | None = None,
        timezone: str = "Europe/London",
        language: str | None = None,
    ) -> TimerInfo | None:
        """Build a TimerInfo object from a sentence."""

        # Sentence could be a datetime
        if (
            language
            and language.split("-", 1)[0] != "en"
            and (translator := Translator.get(self._hass))
        ):
            sentence = await translator.translate_time(sentence, locale=language)
            _LOGGER.debug("Translated time from %s to English: %s", language, sentence)

            # if using llm translation, result will be a datetime or duration, so we can use the existing functions to convert to TimerInfo
            if TimerHelpers.is_datetime_string(sentence):
                return TimerHelpers.build_timer_info_from_datetime(
                    sentence, timezone=timezone, language=language
                )

        # Encode to timer info
        encoder = SentenceEncoder(self._hass, self._config)
        timer_info = await encoder.encode(sentence)

        if not timer_info:
            # The encoder may not be able to parse the sentence for complex asks.
            # So we can use the LLM encoder to try and parse it
            if USE_LLM_FOR_TIMER_ENHANCEMENT:
                _LOGGER.debug("Using LLM to parse sentence: %s", sentence)
                llm_encoder = LLMSentenceEncoder(self._hass, source, language)
                llm_timer_info = await llm_encoder.encode(sentence)

                _LOGGER.debug(
                    "LLM Response for sentence '%s': %s", sentence, llm_timer_info
                )

                return llm_timer_info

        _LOGGER.debug("Encoded sentence time to TimerInfo: %s", timer_info)
        return timer_info


class TimerManagerServices:
    """Class to hold timer manager service names."""

    ATTR_JUST_EXPIRED = "just_expired"

    SET_TIMER_SERVICE_SCHEMA = vol.Schema(
        {
            vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
            vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
            vol.Required(ATTR_TYPE): str,
            vol.Optional(ATTR_NAME): str,
            vol.Optional(ATTR_LANGUAGE): str,
            vol.Required(ATTR_TIME): str,
            vol.Optional(ATTR_EXTRA): vol.Schema({}, extra=vol.ALLOW_EXTRA),
        }
    )

    CANCEL_TIMER_SERVICE_SCHEMA = vol.Schema(
        {
            vol.Exclusive(ATTR_TIMER_ID, "target"): str,
            vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
            vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
            vol.Exclusive(ATTR_REMOVE_ALL, "target"): bool,
            vol.Optional(ATTR_JUST_EXPIRED): bool,
        }
    )

    SNOOZE_TIMER_SERVICE_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_TIMER_ID): str,
            vol.Required(ATTR_TIME): str,
        }
    )

    GET_TIMERS_SERVICE_SCHEMA = vol.Schema(
        {
            vol.Exclusive(ATTR_TIMER_ID, "target"): str,
            vol.Exclusive(ATTR_ENTITY_ID, "target"): cv.entity_id,
            vol.Exclusive(ATTR_DEVICE_ID, "target"): vol.Any(cv.string, None),
            vol.Optional(ATTR_NAME): str,
            vol.Optional(ATTR_INCLUDE_EXPIRED, default=False): bool,
        }
    )

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the timer manager services."""
        self.hass = hass

    def register(self):
        """Register menu manager services."""
        # Init services
        self.hass.services.async_register(
            DOMAIN,
            "set_timer",
            self._async_handle_set_timer,
            schema=self.SET_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "snooze_timer",
            self._async_handle_snooze_timer,
            schema=self.SNOOZE_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "cancel_timer",
            self._async_handle_cancel_timer,
            schema=self.CANCEL_TIMER_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "get_timers",
            self._async_handle_get_timers,
            schema=self.GET_TIMERS_SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    def unregister(self):
        """Unregister menu manager services."""
        for service in ["set_timer", "snooze_timer", "cancel_timer", "get_timers"]:
            self.hass.services.async_remove(DOMAIN, service)

    async def create_response(
        self, response_id: str, timer: Timer | None = None, language: str = "en"
    ) -> str:
        """Create a response string for a timer."""
        translator = Translator.get(self.hass)
        params = {}
        if timer:
            params = {
                "name": timer.name,
                f"time_{language}": timer.timer_info.normalised_sentence,
                "time_en": timer.timer_info.normalised_sentence,
                "snooze_duration": timer.snooze_info.duration
                if timer.snooze_info
                else None,
            }
        return await translator.translate_time_response(response_id, params, language)

    async def _async_handle_set_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        timer_type = call.data.get(ATTR_TYPE)
        name = call.data.get(ATTR_NAME)
        timer_time = call.data.get(ATTR_TIME)
        language = call.data.get(ATTR_LANGUAGE, "en")
        extra_data = call.data.get(ATTR_EXTRA, {})

        if tm := self._get_timer_manager():
            timer = await tm.build_timer(
                device_id=device_id,
                entity_id=entity_id,
                timer_class=timer_type,
                name=name,
                timer_value=timer_time,
                source="service_call",
                language=language,
                extra_info=extra_data,
            )

            if timer:
                response_id, timer = await tm.add_timer(
                    timer=timer,
                    start=True,
                )

                response = await self.create_response(response_id, timer, language)
                return {
                    "timer_id": timer.id if timer else None,
                    "timer": timer or None,
                    "response": response,
                }
        response = await self.create_response("timer_error", language=language)
        return {"response": response}

    async def _async_handle_snooze_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a set timer service call."""
        timer_id = call.data.get(ATTR_TIMER_ID)
        snooze_time = call.data.get(ATTR_TIME)
        language = call.data.get(ATTR_LANGUAGE, "en")

        if tm := self._get_timer_manager():
            timer = tm.search_timers(timer_id=timer_id)

            if timer:
                response_id, timer = await tm.snooze_timer(
                    timer_id=timer_id, minutes=snooze_time
                )
                response = await self.create_response(
                    response_id, timer, language=language
                )
                return {
                    "timer_id": timer.id if timer else None,
                    "timer": timer.to_dict() if timer else None,
                    "response": response,
                }
        response = await self.create_response("timer_error", language=language)
        return {"response": response}

    async def _async_handle_cancel_timer(self, call: ServiceCall) -> ServiceResponse:
        """Handle a cancel timer service call."""
        timer_id = call.data.get(ATTR_TIMER_ID)
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        cancel_all = call.data.get(ATTR_REMOVE_ALL, False)
        just_expired = call.data.get(self.ATTR_JUST_EXPIRED, False)

        if any([timer_id, entity_id, device_id, cancel_all]):
            if tm := self._get_timer_manager():
                result = await tm.cancel_timer(
                    timer_id=timer_id,
                    device_id=device_id,
                    entity_id=entity_id,
                    cancel_all=cancel_all,
                    expired_only=just_expired,
                )
                response = await self.create_response(
                    "timer_cancelled" if result else "timer_not_found"
                )
                return {"response": response}
        return {"error": "no attribute supplied"}

    async def _async_handle_get_timers(self, call: ServiceCall) -> ServiceResponse:
        """Handle a get timers service call."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        device_id = call.data.get(ATTR_DEVICE_ID)
        timer_id = call.data.get(ATTR_TIMER_ID)
        name = call.data.get(ATTR_NAME)
        include_expired = call.data.get(ATTR_INCLUDE_EXPIRED, False)

        if tm := self._get_timer_manager():
            result = tm.search_timers(
                timer_id=timer_id,
                device_id=device_id,
                entity_id=entity_id,
                name=name,
                include_expired=include_expired,
            )

            # Keep compatibility with old automation scripts that expect
            timers = [timer.to_dict() for timer in result] if result else []
            for timer in timers:
                timer["expiry"] = {}
                timer["expiry"]["text"] = timer["remaining_info"]["text"]
                timer["duration"] = timer["timer_info"]["normalised_sentence"]

            return {"result": timers}
        return {"result": []}

    def _get_timer_manager(self) -> TimerManager | None:
        """Get the timer manager for an entity id."""
        return TimerManager.get(self.hass)
