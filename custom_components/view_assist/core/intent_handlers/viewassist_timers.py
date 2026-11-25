"""Override HassStartTimer intent handler."""

import logging
import traceback

import voluptuous as vol

from homeassistant.components.intent.timers import TimerNotFoundError
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ID, ATTR_NAME
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.helpers.intent import (
    Intent,
    IntentHandleError,
    IntentResponse,
    IntentResponseErrorCode,
)
from homeassistant.util import dt as dt_util

from ..timers import Duration, TimerClass, TimerHelpers, TimerManager, TimerStatus  # noqa: TID252
from . import IntentOverrideHandler

_LOGGER = logging.getLogger(__name__)


class VAStartTimerIntentHandler(IntentOverrideHandler):
    """Intent handler for Start Timer intents."""

    intent_type = intent.INTENT_START_TIMER
    description = f"""
        Sets a timer, alarm, reminder or command.
        A type slot must be provided to specify whether it is an alarm, timer, reminder or command.
        If an interval or duration is specified, it should be provided in days, hours, minutes and seconds slots and will be used to set the timer to expire after that duration.
        If a time or datetime is specified, it should be provided in iso format in the time slot.
        If a time is provided, it must be in the future and will be used to set the timer to expire at that time.
        If the time requested has passed, set for the next occurrence of that time in the future.
        If both a time and a duration are provided, the time will take precedence and the duration will be ignored.
        Reminders and delayed commands must have a name slot that is the name of the reminder or the command to run.
        Alarms and timers can have an optional name slot which should be derived from the request.  Do not provide a name if not specified in the request and especially do not provide a name that is the type.
        The current datetime is {dt_util.now().strftime("%Y-%m-%d %H:%M:%S")}.
        """

    @property
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Required(vol.Any("hours", "minutes", "seconds", "time")): vol.Any(
                cv.positive_int, cv.datetime, cv.string
            ),
            vol.Optional("name"): cv.string,
            vol.Optional("conversation_command"): cv.string,
            vol.Optional("type"): cv.string,
        }

    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Handle the intent with custom logic."""
        _LOGGER.debug(
            "%s invoked with intent: %s -> %s -> %s -> %s",
            self.__class__.__name__,
            intent_obj.intent_type,
            intent_obj.slots,
            intent_obj.context.as_dict(),
            intent_obj.conversation_agent_id or intent_obj.platform,
        )

        # Timer class and name
        timer_class = TimerClass.TIMER
        name = ""
        if intent_obj.slots and "name" in intent_obj.slots:
            name = intent_obj.slots["name"]["value"]

        if intent_obj.slots and "type" in intent_obj.slots:
            timer_class = intent_obj.slots["type"]["value"]

        if intent_obj.slots and "conversation_command" in intent_obj.slots:
            timer_class = TimerClass.COMMAND
            name = intent_obj.slots["conversation_command"]["value"]

        # Get area
        # area = intent_obj.slots.get("preferred_area_id", {"value": None})["value"]

        timer_value = None

        _LOGGER.debug("Creating %s with slots: %s", timer_class, intent_obj.slots)

        if any(t in intent_obj.slots for t in ("days", "hours", "minutes", "seconds")):
            timer_value = Duration(
                days=int(intent_obj.slots.get("days", {"value": 0})["value"]),
                hours=int(intent_obj.slots.get("hours", {"value": 0})["value"]),
                minutes=int(intent_obj.slots.get("minutes", {"value": 0})["value"]),
                seconds=int(intent_obj.slots.get("seconds", {"value": 0})["value"]),
            )
        elif "time" in intent_obj.slots:
            # Time could be a sentence or a datetime
            time_slot = intent_obj.slots["time"]["value"]
            try:
                # Test if it is a datetime
                timer_value = dt_util.parse_datetime(time_slot, raise_on_error=True)
                # ensure has local timezone set
                if timer_value and not timer_value.tzinfo:
                    timer_value = dt_util.as_local(timer_value)

            except ValueError:
                # Must be a sentence
                timer_value = str(time_slot)

        if timer_value:
            try:
                timer_manager = TimerManager.get(intent_obj.hass)

                # If day of week slot is povided, add it to timer_value
                if "dayofweek" in intent_obj.slots:
                    dayofweek = intent_obj.slots["dayofweek"]["value"]
                    timer_value = dayofweek + " " + str(timer_value)

                timer = await timer_manager.build_timer(
                    source=intent_obj.conversation_agent_id or intent_obj.platform,
                    device_id=intent_obj.device_id,
                    timer_class=timer_class,
                    name=name,
                    timer_value=timer_value,
                    language=intent_obj.language,
                    extra_info={"sentence": intent_obj.text_input},
                )

                if timer:
                    await timer_manager.add_timer(timer)
                    response = intent_obj.create_response()
                    response_slots = {}
                    response_slots["class"] = timer.timer_class
                    response_slots["type"] = timer.timer_type
                    if timer.timer_type == "interval":
                        if timer.timer_info.days:
                            response_slots["days"] = str(timer.timer_info.days)
                        if timer.timer_info.hours:
                            response_slots["hours"] = str(timer.timer_info.hours)
                        if timer.timer_info.minutes:
                            response_slots["minutes"] = str(timer.timer_info.minutes)
                        if timer.timer_info.seconds:
                            response_slots["seconds"] = str(timer.timer_info.seconds)
                    else:
                        response_slots["dayofweek"] = str(timer.timer_info.dayofweek)
                        response_slots["time"] = (
                            TimerHelpers.get_datetime_from_timestamp(
                                timer.timer_info.expires_at, timer.timer_info.tz
                            ).strftime("%H:%M")
                        )
                    if timer.name:
                        response_slots["name"] = timer.name
                    response.async_set_speech_slots(response_slots)
                    return response
            except Exception as e:  # noqa: BLE001
                _LOGGER.error(
                    "Failed to set %s for %s: %s\n%s",
                    timer_class,
                    intent_obj.text_input,
                    e,
                    traceback.format_exc(),
                )

        response = intent_obj.create_response()
        response.async_set_error(
            IntentResponseErrorCode.FAILED_TO_HANDLE,
            f"Failed to set {timer_class} for {intent_obj.slots.get('time', {}).get('value') or intent_obj.text_input}",
        )
        return response


class VACancelTimerIntentHandler(IntentOverrideHandler):
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

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent with custom logic."""
        _LOGGER.info(
            "%s invoked with intent: %s -> %s -> %s -> %s",
            self.__class__.__name__,
            intent_obj.intent_type,
            intent_obj.slots,
            intent_obj.context.as_dict(),
            intent_obj.assistant,
        )

        if intent_obj.intent_type == intent.INTENT_CANCEL_ALL_TIMERS:
            return await self.cancel_all_timers(intent_obj)

        return await self.cancel_timer(intent_obj)

    async def cancel_all_timers(
        self, intent_obj: intent.Intent
    ) -> intent.IntentResponse:
        """Cancel all active timers."""
        timer_manager = TimerManager.get(intent_obj.hass)
        device_info = self.get_device_info(intent_obj)
        timers = timer_manager.search_timers(
            entity_id=device_info.entity_id if device_info else None,
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

    async def cancel_timer(self, intent_obj: intent.Intent) -> intent.IntentResponse:
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
            for t in (
                "start_days",
                "start_hours",
                "start_minutes",
                "start_seconds",
                "days",
                "hours",
                "minutes",
                "seconds",
            )
        ):
            slot_duration = Duration(
                days=slots.get("start_days", slots.get("days", {"value": 0}))["value"]
                if slots
                else 0,
                hours=slots.get("start_hours", slots.get("hours", {"value": 0}))[
                    "value"
                ]
                if slots
                else 0,
                minutes=slots.get("start_minutes", slots.get("minutes", {"value": 0}))[
                    "value"
                ]
                if slots
                else 0,
                seconds=slots.get("start_seconds", slots.get("seconds", {"value": 0}))[
                    "value"
                ]
                if slots
                else 0,
            )

        device_info = self.get_device_info(intent_obj)
        matching_timers = timer_manager.search_timers(
            entity_id=device_info.entity_id if device_info else None,
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


class VATimerStatusIntentHandler(IntentOverrideHandler):
    """Intent handler for Timer Status intents."""

    intent_type = intent.INTENT_TIMER_STATUS
    description = f"""
        Reports the current status of any timers, alarms, reminders and delayed commands.
        If no timers are active, the response will indicate that there are no active timers.
        You should respond in a concise manner, providing any name and the remaining time if the timer class is timer or reminder.
        If the timer type is time provide an expiry time for alarms in a clear and understandable format.
        Todays date is {dt_util.now().strftime("%Y-%m-%d")}.
        """

    @property
    def slot_schema(self) -> dict | None:
        """Return a slot schema."""
        return {
            vol.Optional("type"): cv.string,
            vol.Optional("name"): cv.string,
        }

    async def async_handle(self, intent_obj: Intent) -> IntentResponse:
        """Handle the intent with custom logic."""
        _LOGGER.warning(
            "%s invoked with intent: %s -> %s -> %s -> %s",
            self.__class__.__name__,
            intent_obj.intent_type,
            intent_obj.slots,
            intent_obj.context.as_dict(),
            intent_obj.assistant,
        )

        timer_manager = TimerManager.get(intent_obj.hass)

        slots = self.async_validate_slots(intent_obj.slots)

        slot_time = None
        if "time" in slots:
            requested_time = dt_util.parse_datetime(str(slots["time"]["value"]))
            slot_time = requested_time.timestamp() if requested_time else None

        slot_duration = None
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

        # For VA custom sentences we use days, hours, minutes and seconds slots.
        elif any(t in slots for t in ("days", "hours", "minutes", "seconds")):
            slot_duration = Duration(
                days=slots.get("days", {"value": 0})["value"] if slots else 0,
                hours=slots.get("hours", {"value": 0})["value"] if slots else 0,
                minutes=slots.get("minutes", {"value": 0})["value"] if slots else 0,
                seconds=slots.get("seconds", {"value": 0})["value"] if slots else 0,
            )

        device_info = self.get_device_info(intent_obj)
        matching_timers = timer_manager.search_timers(
            entity_id=device_info.entity_id if device_info else None,
            timer_id=slots.get("timer_id", {}).get("value") if slots else None,
            name=slots.get("name", {}).get("value") if slots else None,
            expires_at=slot_time,
            duration=slot_duration,
            timer_class=slots.get("type", {}).get("value") if slots else None,
        )

        statuses = []
        for timer in matching_timers:
            statuses.append(  # noqa: PERF401
                {
                    "area": intent_obj.slots.get("preferred_area_id", {"value": None})[
                        "value"
                    ],
                    ATTR_ID: timer.id,
                    "timer_class": timer.timer_class,
                    "timer_type": timer.timer_type,
                    ATTR_NAME: timer.name,
                    ATTR_DEVICE_ID: timer.conversation_device_id,
                    "language": "",
                    "is_active": timer.status
                    in (TimerStatus.RUNNING, TimerStatus.SNOOZED),
                    "start_days": int(timer.timer_info.days),
                    "start_hours": int(timer.timer_info.hours),
                    "start_minutes": int(timer.timer_info.minutes),
                    "start_seconds": int(timer.timer_info.seconds),
                    "expiry_time": timer.timer_info.expires_at,
                    "days_left": int(timer.remaining_info.duration.days),
                    "hours_left": int(timer.remaining_info.duration.hours),
                    "minutes_left": int(timer.remaining_info.duration.minutes),
                    "seconds_left": int(timer.remaining_info.duration.seconds),
                    "rounded_days_left": int(timer.remaining_info.duration.days),
                    "rounded_hours_left": int(timer.remaining_info.duration.hours),
                    "rounded_minutes_left": int(timer.remaining_info.duration.minutes),
                    "rounded_seconds_left": int(timer.remaining_info.duration.seconds),
                    "total_seconds_left": int(timer.remaining_info.duration.seconds),
                    "dayofweek": timer.timer_info.dayofweek,
                    "time": timer.timer_info.time,
                }
            )

        response = intent_obj.create_response()
        response.async_set_speech_slots(
            {
                "class": slots.get("type", {}).get("value") if slots else "timer",
                "timers": statuses,
            }
        )
        return response


class TimerUnableToDecodeError(IntentHandleError):
    """Error when a timer could not be decoded."""

    def __init__(self) -> None:
        """Initialize error."""
        super().__init__("Unable to decode timer", "no_intent")
