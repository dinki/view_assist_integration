"""Override intent handler."""

import logging

import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID, ATTR_ID, ATTR_NAME
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.util import dt as dt_util

from ..timers import Duration, TimerManager, TimerStatus  # noqa: TID252

_LOGGER = logging.getLogger(__name__)


class VATimerStatusIntentHandler(intent.IntentHandler):
    """Intent handler for Set Alarm intents."""

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

        matching_timers = timer_manager.search_timers(
            entity_id=extra_data.get("entity_id") if extra_data else None,
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
