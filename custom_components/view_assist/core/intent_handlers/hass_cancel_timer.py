"""Override intent handler."""

import logging

import voluptuous as vol

from homeassistant.components.intent.timers import TimerNotFoundError
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.util import dt as dt_util

from ..timers import Duration, TimerClass, TimerHelpers, TimerManager  # noqa: TID252

_LOGGER = logging.getLogger(__name__)


class VACancelTimerIntentHandler(intent.IntentHandler):
    """Intent handler for Cancel Timer intents."""

    intent_type = intent.INTENT_CANCEL_TIMER
    description = f"""
        Cancels an active timer, alarm, reminder or command.
        A type slot must be provided to specify whether it is an alarm, timer, reminder or command that is to be cancelled.
        If no other slots are provided, the earliest timer by type will be cancelled.
        If a timer_id slot is provided, the timer with that id will be cancelled.
        If a name slot is provided, the timer with that name will be cancelled.
        If a time in iso format is provided, the timer with that expiry time will be cancelled.
        If start_days, start_hours, start_minutes and start_seconds slots are provided, the timer with that duration will be cancelled.
        If multiple timers match the provided slots, the first matching timer will be cancelled.
        If no slots are provided and there are no active timers, the response will indicate that there are no active timers.
        The current time is {dt_util.now().strftime("%Y-%m-%d %H:%M:%S")}.
        """

    @property
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Any(
                "start_days", "start_hours", "start_minutes", "start_seconds"
            ): cv.positive_int,
            vol.Optional("time"): vol.Any(cv.datetime, cv.string),
            vol.Optional("timer_id"): cv.string,
            vol.Optional("name"): cv.string,
            vol.Optional("conversation_command"): cv.string,
            vol.Optional("type"): cv.string,
        }

    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
        """Handle the intent with custom logic."""
        _LOGGER.warning(
            "%s invoked with intent: %s -> %s -> %s -> %s",
            self.__class__.__name__,
            intent_obj.intent_type,
            intent_obj.slots,
            intent_obj.context.as_dict(),
            intent_obj.assistant,
        )

        if intent_obj.intent_type == intent.INTENT_CANCEL_ALL_TIMERS:
            return await self.cancel_all_timers(intent_obj, extra_data)

        return await self.cancel_timer(intent_obj, extra_data)

    async def cancel_all_timers(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
        """Cancel all active timers."""
        timer_manager = TimerManager.get(intent_obj.hass)
        timers = timer_manager.search_timers(
            entity_id=extra_data.get("entity_id") if extra_data else None,
            include_expired=True,
        )

        cancelled = 0

        for timer in timers:
            await timer_manager.cancel_timer(timer.id)
            cancelled += 1
            _LOGGER.info(
                "Cancelled timer: %s",
                timer.name or timer.timer_info.normalised_sentence,
            )

        response = intent_obj.create_response()
        response.async_set_speech_slots({"canceled": cancelled})
        return response

    async def cancel_timer(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
        """Cancel a specific timer."""
        timer_manager = TimerManager.get(intent_obj.hass)
        slots = self.async_validate_slots(intent_obj.slots)

        expires_at = None
        slot_duration = None

        if "time" in slots:
            slot_time = slots["time"]["value"]
            if TimerHelpers.is_datetime_string(slot_time):
                timer_info = TimerHelpers.build_timer_info_from_datetime(
                    slot_time,
                    timezone=intent_obj.hass.config.time_zone,
                    language=intent_obj.language,
                )
            else:
                timer_info = await timer_manager.build_timer_info_from_sentence(
                    slot_time,
                    timezone=intent_obj.hass.config.time_zone,
                    language=intent_obj.language,
                )
            if timer_info:
                expiry = TimerHelpers.get_expiry_from_timerinfo(timer_info)
                expires_at = round(expiry.timestamp()) if expiry else 0

        if any(
            t in slots
            for t in ("start_days", "start_hours", "start_minutes", "start_seconds")
        ):
            slot_duration = Duration(
                days=slots.get("start_days", {"value": 0})["value"] if slots else 0,
                hours=slots.get("start_hours", {"value": 0})["value"] if slots else 0,
                minutes=slots.get("start_minutes", {"value": 0})["value"]
                if slots
                else 0,
                seconds=slots.get("start_seconds", {"value": 0})["value"]
                if slots
                else 0,
            )

        matching_timers = timer_manager.search_timers(
            entity_id=extra_data.get("entity_id") if extra_data else None,
            timer_id=slots.get("timer_id", {}).get("value") if slots else None,
            name=slots.get("name", {}).get("value") if slots else None,
            expires_at=expires_at,
            duration=slot_duration,
            timer_class=slots.get("type", {}).get("value") or TimerClass.TIMER,
            include_expired=True,
        )

        number_of_matches = len(matching_timers)

        if number_of_matches == 1:
            timer = matching_timers[0]
            await timer_manager.cancel_timer(timer.id)
            _LOGGER.info("Cancelled timer: %s", timer.name)
            response = intent_obj.create_response()
            slots = {}
            if timer.timer_class:
                slots["class"] = timer.timer_class
            if timer.name:
                slots["name"] = timer.name
            if timer.timer_info.normalised_sentence:
                slots["interval_or_time"] = timer.timer_info.normalised_sentence

            response.async_set_speech_slots(slots)
            return response

        if number_of_matches > 1:
            _LOGGER.warning(
                "Multiple timers match the provided slots. Not cancelling any timer"
            )
            response = intent_obj.create_response()
            response.async_set_speech(
                f"You have multiple {matching_timers[0].timer_class}s matching that criteria. Please specify which one to cancel."
            )
            return response
        raise TimerNotFoundError


class VACancelAllTimersIntentHandler(VACancelTimerIntentHandler):
    """Intent handler for Cancel All Timers intents."""

    intent_type = intent.INTENT_CANCEL_ALL_TIMERS
    description = f"""
        Cancels all active timers.
        If no timers are active, the response will indicate that there are no active timers.
        Todays date is {dt_util.now().strftime("%Y-%m-%d")}.
        """
