"""Override HassStartTimer intent handler."""

import logging
import traceback

import voluptuous as vol

from homeassistant.helpers import config_validation as cv, intent
from homeassistant.util import dt as dt_util

from ..timers import TimerClass, TimerManager, Duration, TimerHelpers  # noqa: TID252

_LOGGER = logging.getLogger(__name__)


class VAStartTimerIntentHandler(intent.IntentHandler):
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

    async def async_handle(
        self, intent_obj: intent.Intent, extra_data: dict | None = None
    ) -> intent.IntentResponse:
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
            except Exception as e:
                _LOGGER.error(
                    "Failed to set %s for %s: %s\n%s",
                    timer_class,
                    intent_obj.text_input,
                    e,
                    traceback.format_exc(),
                )

        response = intent_obj.create_response()
        response.async_set_error(
            intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
            f"Failed to set {timer_class} for {intent_obj.slots.get('time', {}).get('value') or intent_obj.text_input}",
        )
        return response


class TimerUnableToDecodeError(intent.IntentHandleError):
    """Error when a timer could not be decoded."""

    def __init__(self) -> None:
        """Initialize error."""
        super().__init__("Unable to decode timer", "no_intent")
