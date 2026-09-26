"""Timer manager for View Assist."""

import asyncio
import datetime as dt
import logging
import zoneinfo

from homeassistant.components.intent import (
    TIMER_DATA,
    TimerEventType,
    TimerInfo as IntentTimerInfo,
    TimerManager as IntentTimerManager,
)
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import Context, Event, HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from ...const import DEFAULT_ALARM_SOUND_FILE, DOMAIN, VACA_DOMAIN  # noqa: TID252
from ...helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_mic_device_domain,
    get_mic_device_id_from_entity_id,
    normalize_name,
)
from ...typed import VAConfigEntry, VAEvent, VAEventType, VATimeFormat  # noqa: TID252
from .helpers import TimerHelpers
from .storage import VATimerStore
from .typed import (
    REMINDER_ANNOUNCE_INTERVAL,
    VA_COMMAND_EVENT_PREFIX,
    VA_EVENT_PREFIX,
    Duration,
    SnoozeInfo,
    Timer,
    TimerClass,
    TimerEvent,
    TimerStatus,
)

_LOGGER = logging.getLogger(__name__)


class TimerHandler:
    """Class to handle VA timers."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.tz: zoneinfo.ZoneInfo = zoneinfo.ZoneInfo(self.hass.config.time_zone)

        self.store = VATimerStore(hass)
        self.timer_tasks: dict[str, asyncio.Task] = {}
        self.sounding_tasks: dict[str, asyncio.Task] = {}
        self._remove_alarm_stop_listener = None

    async def start(self):
        """Start the timer handler."""
        # Initialise timer store
        await self.store.load()

        # Load and start any existing timers from storage
        if self.store.timers:
            # Remove any in expired/sounding status on restart as whatever was
            # announcing/sounding them will not have survived the restart
            stale_timers = [
                timer_id
                for timer_id, timer in self.store.timers.items()
                if timer.status in (TimerStatus.EXPIRED, TimerStatus.SOUNDING)
            ]
            for timer_id in stale_timers:
                self.store.timers.pop(timer_id, None)

            for timer in self.store.timers.values():
                _LOGGER.debug("Starting timer: %s, %s", timer.id, timer.name)
                await self.start_timer(timer)

        # Listen for alarms stopping (streamed or VACA) so their timer can be
        # cancelled once whatever was sounding them has finished
        self._remove_alarm_stop_listener = self.hass.bus.async_listen(
            "vaca_alarm_stop", self._handle_alarm_stop_event
        )

    async def stop(self):
        """Stop the timer handler."""
        if self._remove_alarm_stop_listener:
            self._remove_alarm_stop_listener()
            self._remove_alarm_stop_listener = None

        # Cancel any timer and sounding tasks
        for task in (*self.timer_tasks.values(), *self.sounding_tasks.values()):
            task.cancel()
        self.timer_tasks = {}
        self.sounding_tasks = {}

    async def add_timer(self, timer: Timer, start: bool = True) -> tuple:
        """Add timer to store."""

        if duplicate_timer := self.is_duplicate_timer(timer):
            return (
                "timer_already_exists",
                duplicate_timer.to_dict() if duplicate_timer else None,
            )

        self.store.timers[timer.id] = timer
        await self.store.save()

        if start:
            await self.start_timer(timer)

        return (
            "timer_named_set" if timer.name else "timer_set",
            timer,
        )

    async def start_timer(self, timer: Timer):
        """Start timer running."""

        total_seconds = round(timer.timer_info.expires_at - dt_util.now().timestamp())

        # Fire event if total seconds -ve
        # likely caused by timer expiring during restart
        if total_seconds < 1:
            await self._timer_finished(timer.id)
        else:
            if (
                timer.timer_info.pre_expire_warning
                and timer.timer_info.pre_expire_warning >= total_seconds
            ):
                # Create task to wait for timer duration with no warning
                self.timer_tasks[timer.id] = self.config.async_create_background_task(
                    self.hass,
                    self._wait_for_timer(
                        timer.id,
                        total_seconds,
                        timer.timer_info.expires_at,
                        fire_warning=False,
                    ),
                    name=f"Timer {timer.id}",
                )
                _LOGGER.debug(
                    "Started %s timer for %ss, with no warning event",
                    timer.name,
                    total_seconds,
                )
            else:
                # Create task to wait for timer duration minus any pre_expire_warning time
                self.timer_tasks[timer.id] = self.config.async_create_background_task(
                    self.hass,
                    self._wait_for_timer(
                        timer.id,
                        total_seconds - timer.timer_info.pre_expire_warning,
                        timer.timer_info.expires_at,
                    ),
                    name=f"Timer {timer.id}",
                )
                _LOGGER.debug(
                    "Started %s timer for %ss, with warning event at %ss",
                    timer.name,
                    total_seconds,
                    total_seconds - timer.timer_info.pre_expire_warning,
                )

            # Support non VACA devices like ESPHome that have their own timer manager and can handle the timer expiry themselves
            device_domain = get_mic_device_domain(self.hass, timer.entity_id)
            if device_domain == "esphome":
                await self._start_intent_timer(timer)

            if timer.status != TimerStatus.RUNNING:
                await self.store.update_status(timer.id, TimerStatus.RUNNING)

                # Fire event - done here to only fire if new timer started not
                # existing timer restarted after HA restart
                await self._fire_event(timer, TimerEvent.STARTED)

    async def snooze_timer(
        self, timer_id: str, minutes: int
    ) -> tuple[str | None, Timer | None]:
        """Snooze expired timer.

        This will set the timer expire to now plus duration on an expired timer
        and set the status to snooze.  Then re-run the timer.
        """
        timer = self.store.timers.get(timer_id)
        if timer and timer.status == TimerStatus.EXPIRED:
            expiry = dt_util.now() + dt.timedelta(
                minutes=minutes,
            )
            timer.timer_info.expires_at = expiry.timestamp()
            count = timer.snooze_info.count + 1 if timer.snooze_info else 1
            timer.snooze_info = SnoozeInfo(
                count=count,
                duration=Duration(minutes=minutes),
                snoozed_at=dt_util.now().timestamp(),
            )

            await self.store.update_status(timer_id, TimerStatus.SNOOZED)
            await self.start_timer(timer)
            await self._fire_event(timer, TimerEvent.SNOOZED)

            return (
                "timer_named_snoozed" if timer.name else "timer_snoozed",
                timer.to_dict(),
            )
        return "timer_error", timer or None

    async def cancel_timer(
        self,
        timer_id: str | None = None,
        device_id: str | None = None,
        entity_id: str | None = None,
        name: str | None = None,
        duration: Duration | None = None,
        cancel_all: bool = False,
        expired_only: bool = False,
        fire_event: bool = True,
    ) -> bool:
        """Cancel timer by timer id, device id or all."""

        # Iterate through timers and cancel any that match all the supplied the criteria
        matched: dict[str, Timer] = {}
        for timer in self.store.timers.values():
            if cancel_all:
                matched[timer.id] = timer
                continue
            if expired_only and timer.status != TimerStatus.EXPIRED:
                continue
            if timer_id and timer.id != timer_id:
                continue
            if device_id and timer.conversation_device_id != device_id:
                continue
            if entity_id and timer.entity_id != entity_id:
                continue
            if name and normalize_name(timer.name or "") != normalize_name(name):
                continue
            if duration:
                if not timer.timer_info:
                    continue
                if (
                    timer.timer_info.days != duration.days
                    or timer.timer_info.hours != duration.hours
                    or timer.timer_info.minutes != duration.minutes
                    or timer.timer_info.seconds != duration.seconds
                ):
                    continue

            # If we get here, the timer matches all the supplied criteria
            matched[timer.id] = timer

        if matched:
            for timerid, timer in matched.items():
                device_domain = get_mic_device_domain(self.hass, timer.entity_id)
                if device_domain == "esphome":
                    await self._cancel_intent_timer(timerid)

                if timer.status == TimerStatus.SOUNDING:
                    await self._stop_sounding_alarm(timer)

                if await self.store.cancel_timer(timerid):
                    _LOGGER.debug("Cancelled timer: %s", timerid)
                    wait_task = self.timer_tasks.pop(timerid, None)
                    sounding_task = self.sounding_tasks.pop(timerid, None)
                    for task in (wait_task, sounding_task):
                        if task and not task.done():
                            task.cancel()
                    if fire_event:
                        await self._fire_event(timer, TimerEvent.CANCELLED)
            return True
        return False

    async def cancel_sounding_timers(self, entity_id: str) -> bool:
        """Cancel any timers currently sounding for a View Assist entity.

        Stops whatever is sounding the alarm/reminder (announce loop, alarm
        streamer or VACA) and cancels the timer(s) that triggered it.
        """
        sounding_timer_ids = [
            timer.id
            for timer in self.store.timers.values()
            if timer.entity_id == entity_id and timer.status == TimerStatus.SOUNDING
        ]

        if not sounding_timer_ids:
            return False

        for timerid in sounding_timer_ids:
            _LOGGER.debug("Cancelling sounding timer: %s", timerid)
            await self.cancel_timer(timer_id=timerid, fire_event=False)

        return True

    def get_timers(
        self,
        timer_id: str = "",
        device_id: str = "",
        entity_id: str = "",
        include_expired: bool = False,
        sort: bool = True,
        language: str = "en",
    ) -> list[Timer]:
        """Get list of timers.

        Optionally supply timer_id, device_id or entity id to filter the returned list
        """

        # Iterate through timers and cancel any that match all the supplied the criteria
        timers: list[Timer] = []
        for timer in self.store.timers.values():
            if not include_expired and timer.status == TimerStatus.EXPIRED:
                continue
            if timer_id and timer.id != timer_id:
                continue
            if device_id and timer.conversation_device_id != device_id:
                continue
            if entity_id and timer.entity_id != entity_id:
                continue

            # If esphome device, filter by timers registered with timer manager
            # If using stop to cancel alarm on HAVPE, does not use the cancel service
            # and therefore the alarm is left behind in expired state.  So filter out any timers
            # that are not still registered with the intent timer manager
            device_domain = get_mic_device_domain(self.hass, timer.entity_id)
            tm: IntentTimerManager = self.hass.data[TIMER_DATA]
            if device_domain == "esphome":
                timers = [timer for timer in timers if timer.id in tm.timers]

            # If we get here, the timer matches all the supplied criteria
            # Get the expiry info and add to the return list
            timer.remaining_info = TimerHelpers.build_remaining_info_from_timer(
                timer=timer,
                language=language,
                h24format=(
                    self.config.runtime_data.dashboard.display_settings.time_format
                    == VATimeFormat.HOUR_24
                ),
            )
            # Catch for expired timers that have no remaining info, which can happen if the timer was expired during a restart
            if not timer.remaining_info:
                self.store.update_status(timer.id, TimerStatus.EXPIRED)
                if include_expired:
                    timers.append(timer)
            else:
                timers.append(timer)

        if sort and timers:
            timers = sorted(timers, key=lambda d: d.remaining_info.total_seconds)

        return timers

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

        _LOGGER.debug("Search timers with parameters: %s", locals())

        if timer_id:
            return self.get_timers(timer_id=timer_id)

        # Calling get timers will pre filter the list by device_id, entity_id and include_expired
        # And sort the list if required.  Then we can filter by the other criteria
        filter_list: list[Timer] = self.get_timers(
            device_id=device_id,
            entity_id=entity_id,
            include_expired=include_expired,
            sort=sort,
        )

        # Providing a name will check in name and alt_name and timer_info.sentence and expiry_info.text for a match
        if name:
            name = normalize_name(name)
            filter_list = [
                timer
                for timer in filter_list
                if normalize_name(timer.name or "") == name
                or normalize_name(timer.timer_info.request_sentence or "") == name
                or normalize_name(timer.timer_info.normalised_sentence or "") == name
                or normalize_name(timer.remaining_info.text or "") == name
            ]
        if expires_at:
            filter_list = [
                timer
                for timer in filter_list
                if timer.timer_info.expires_at == expires_at
            ]
        if duration:
            filter_list = [
                timer
                for timer in filter_list
                if timer.timer_info
                and timer.timer_info.days == duration.days
                and timer.timer_info.hours == duration.hours
                and timer.timer_info.minutes == duration.minutes
                and timer.timer_info.seconds == duration.seconds
            ]

        # If more than 1 matching timer and timer_class supplied, filter by timer_class
        # To not be pedantic, if only 1 matching timer, do not force use of type and assume this is what is meant
        if len(filter_list) > 1 and timer_class:
            filter_list = [
                timer for timer in filter_list if timer.timer_class == timer_class
            ]

        return filter_list

    async def _fire_event(self, timer: Timer, event_type: TimerEvent):
        """Fire timer event on the event bus."""
        event_name = (
            VA_COMMAND_EVENT_PREFIX
            if timer.timer_class == TimerClass.COMMAND
            else VA_EVENT_PREFIX
        ).format(event_type)
        event_data = {"timer_id": timer.id}
        event_data.update(timer.to_dict())
        self.hass.bus.async_fire(event_name, event_data)
        entry = get_config_entry_by_entity_id(self.hass, timer.entity_id)
        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{entry.entry_id}_event",
            VAEvent(
                VAEventType.TIMER_UPDATE,
                {
                    "event": event_type,
                    "event_timer": timer,
                    "timers": [
                        timer.to_dict()
                        for timer in self.get_timers(entity_id=timer.entity_id)
                    ],
                },
            ),
        )
        _LOGGER.debug("Timer event fired: %s - %s", event_name, event_data)

    def is_duplicate_timer(self, timer: Timer) -> Timer | None:
        """Return if same timer already exists."""

        # Get timers for device_id
        existing_device_timers = [
            t for t in self.store.timers.values() if t.entity_id == timer.entity_id
        ]

        if not existing_device_timers:
            return None

        for t in existing_device_timers:
            if t.timer_info.expires_at == timer.timer_info.expires_at:
                return t
        return None

    async def _wait_for_timer(
        self, timer_id: str, seconds: int, expires_at: int, fire_warning: bool = True
    ) -> None:
        """Sleep until timer is up. Timer is only finished if it hasn't been updated."""
        try:
            await asyncio.sleep(seconds)
            timer = self.store.timers.get(timer_id)
            if timer and int(timer.timer_info.expires_at) == int(expires_at):
                if fire_warning and timer.timer_info.pre_expire_warning:
                    await self._pre_expire_warning(timer_id)
                else:
                    await self._timer_finished(timer_id)
        except asyncio.CancelledError:
            pass  # expected when timer is updated

    async def _pre_expire_warning(self, timer_id: str) -> None:
        """Call event on timer pre_expire_warning and then call expire."""
        timer = self.store.timers.get(timer_id)

        if timer and timer.status == TimerStatus.RUNNING:
            await self._fire_event(timer, TimerEvent.WARNING)

            await asyncio.sleep(timer.timer_info.pre_expire_warning)
            await self._timer_finished(timer_id)

    async def _timer_finished(self, timer_id: str) -> None:
        """Call event handlers when a timer finishes."""

        timer = self.store.timers.get(timer_id)

        if not timer:
            return

        self.timer_tasks.pop(timer_id, None)

        # Every timer fires an expired event when it finishes, regardless of class
        await self._fire_event(timer, TimerEvent.EXPIRED)

        if timer.timer_class == TimerClass.REMINDER:
            # Keep announcing the reminder on a repeating interval until it's
            # cancelled - either directly or via cancel_sounding_timers
            await self.store.update_status(timer_id, TimerStatus.SOUNDING)
            self.sounding_tasks[timer_id] = self.config.async_create_background_task(
                self.hass,
                self._repeat_reminder_announcement(timer_id),
                name=f"ReminderAnnounce-{timer_id}",
            )

        elif timer.timer_class == TimerClass.COMMAND:
            from homeassistant.components.conversation import (  # noqa: PLC0415
                async_converse,
            )

            self.hass.async_create_background_task(
                async_converse(
                    self.hass,
                    timer.name,
                    conversation_id=None,
                    context=Context(),
                    language=self.hass.config.language,
                    agent_id=timer.source,
                    device_id=timer.conversation_device_id,
                ),
                "VA timer assist command",
            )
            # Command has been handed off to execute - nothing further sounds
            await self.cancel_timer(
                timer_id=timer_id,
                fire_event=False,
            )

        elif get_mic_device_domain(self.hass, timer.entity_id) == "esphome":
            await self._finish_intent_timer(timer_id)

        elif timer.timer_class in (TimerClass.TIMER, TimerClass.ALARM):
            await self.store.update_status(timer_id, TimerStatus.SOUNDING)
            await self._sound_timer_alarm(timer)

    async def _repeat_reminder_announcement(self, timer_id: str) -> None:
        """Repeat a reminder announcement until the reminder is cancelled."""
        try:
            while True:
                timer = self.store.timers.get(timer_id)
                if not timer or timer.status != TimerStatus.SOUNDING:
                    return

                await self.hass.services.async_call(
                    domain="assist_satellite",
                    service="announce",
                    target={"device_id": timer.conversation_device_id},
                    service_data={
                        "preannounce": True,
                        "message": f"This is your reminder to {timer.name}",
                    },
                    blocking=False,
                )
                await asyncio.sleep(REMINDER_ANNOUNCE_INTERVAL)
        except asyncio.CancelledError:
            # expected when the reminder is cancelled
            await self.store.update_status(timer_id, TimerStatus.EXPIRED)
            self.cancel_timer(timer_id, fire_event=False)

    async def _sound_timer_alarm(self, timer: Timer) -> None:
        """Sound the alarm for an expired timer/alarm.

        Devices that can be streamed to are handled directly via the alarm
        streamer, which fires EVENT_ALARM_SOUND/EVENT_ALARM_STOP itself.
        VACA devices play their own alarm sound natively, so they are simply
        told to start via EVENT_ALARM_SOUND, and are expected to fire
        EVENT_ALARM_STOP themselves once that alarm is dismissed.
        """
        device_domain = get_mic_device_domain(self.hass, timer.entity_id)

        _LOGGER.debug(
            "Sounding alarm for timer %s on device %s (domain: %s)",
            timer.id,
            timer.entity_id,
            device_domain,
        )

        entry = get_config_entry_by_entity_id(self.hass, timer.entity_id)

        if device_domain == VACA_DOMAIN:
            device_id = get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
            self.hass.bus.async_fire("va_alarm_start", {ATTR_DEVICE_ID: device_id})
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{entry.entry_id}_event",
                VAEvent(VAEventType.ALARM_SOUNDING, {"state": True}),
            )
            return

        from ..alarm_streamer import AlarmStreamer  # noqa: PLC0415, TID252

        alarm_streamer = AlarmStreamer.get(self.hass)
        if alarm_streamer:
            await alarm_streamer.alarm_sound(timer.entity_id, DEFAULT_ALARM_SOUND_FILE)
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{entry.entry_id}_event",
                VAEvent(VAEventType.ALARM_SOUNDING, {"state": True}),
            )
        else:
            _LOGGER.error(
                "Alarm streamer not available to sound alarm for %s", timer.entity_id
            )

    async def _stop_sounding_alarm(self, timer: Timer) -> None:
        """Stop whatever is currently sounding a TIMER/ALARM class timer."""
        if timer.timer_class not in (TimerClass.TIMER, TimerClass.ALARM):
            return

        device_domain = get_mic_device_domain(self.hass, timer.entity_id)
        entry = get_config_entry_by_entity_id(self.hass, timer.entity_id)

        if device_domain == VACA_DOMAIN:
            device_id = get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
            self.hass.bus.async_fire("va_alarm_stop", {ATTR_DEVICE_ID: device_id})
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{entry.entry_id}_event",
                VAEvent(VAEventType.ALARM_SOUNDING, {"state": False}),
            )
            return

        from ..alarm_streamer import AlarmStreamer  # noqa: PLC0415, TID252

        alarm_streamer = AlarmStreamer.get(self.hass)
        if alarm_streamer:
            await alarm_streamer.cancel_alarm_sound(timer.entity_id)
            async_dispatcher_send(
                self.hass,
                f"{DOMAIN}_{entry.entry_id}_event",
                VAEvent(VAEventType.ALARM_SOUNDING, {"state": False}),
            )

    async def _handle_alarm_stop_event(self, event: Event) -> None:
        """Cancel any sounding TIMER/ALARM timer whose device just stopped sounding."""
        device_id = event.data.get(ATTR_DEVICE_ID)
        if not device_id:
            return

        for timer in list(self.store.timers.values()):
            if (
                timer.status == TimerStatus.SOUNDING
                and timer.timer_class in (TimerClass.TIMER, TimerClass.ALARM)
                and get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
                == device_id
            ):
                await self.cancel_timer(timer_id=timer.id, fire_event=False)

    # -------------------------------- Intent Timer Handling ---------------------------------
    # Handle intent timers for devices that have their own timer manager like ESPHome and HAVPE
    # ------------------------------------------------------------------------------------------
    async def _start_intent_timer(self, timer: Timer, retry: bool = True) -> None:
        """Send intent to VA intent handler."""
        device_id = get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
        orig_total_seconds = round(timer.timer_info.expires_at - timer.created_at)
        total_seconds = round(timer.timer_info.expires_at - dt_util.now().timestamp())
        _LOGGER.debug(
            "Sending intent timer for device id: %s for %s seconds",
            device_id,
            total_seconds,
        )

        tm: IntentTimerManager = self.hass.data[TIMER_DATA]
        intent_timer = IntentTimerInfo(
            id=timer.id,
            name=timer.name,
            start_hours=0,
            start_minutes=0,
            start_seconds=orig_total_seconds,
            seconds=orig_total_seconds,
            language=timer.extra_info.get("language", "en"),
            device_id=device_id,
            created_at=timer.created_at_monotonic,
            updated_at=timer.created_at_monotonic,
        )
        _LOGGER.debug(
            "Created intent timer created seconds: %s", intent_timer.created_seconds
        )

        # Fill in area/floor info
        device_registry = dr.async_get(self.hass)
        if device_id and (device := device_registry.async_get(device_id)):
            intent_timer.area_id = device.area_id
            area_registry = ar.async_get(self.hass)
            if device.area_id and (
                area := area_registry.async_get_area(device.area_id)
            ):
                intent_timer.area_name = normalize_name(area.name)
                intent_timer.floor_id = area.floor_id

        tm.timers[timer.id] = intent_timer
        if (not intent_timer.conversation_command) and (
            intent_timer.device_id in tm.handlers
        ):
            tm.handlers[intent_timer.device_id](TimerEventType.STARTED, intent_timer)
        elif retry:
            self.config.async_create_background_task(
                self.hass,
                self._retry_start_intent_timer(timer),
                name=f"Retry Intent Timer {timer.id}",
            )

    async def _retry_start_intent_timer(self, timer: Timer) -> None:
        """Retry starting intent timer after delay."""
        # TODO: Can we restart timers when device comes back online instead?
        await asyncio.sleep(10)
        await self._start_intent_timer(timer, retry=False)

    async def _finish_intent_timer(self, timer_id: str) -> None:
        """Finish intent timer by timer id."""
        if timer := self.store.timers.get(timer_id):
            device_id = get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
            tm: IntentTimerManager = self.hass.data[TIMER_DATA]
            if timer := tm.timers.pop(timer_id):
                timer.finish()
                if device_id in tm.handlers:
                    tm.handlers[device_id](TimerEventType.FINISHED, timer)

    async def _cancel_intent_timer(self, timer_id: str) -> bool:
        """Cancel intent timer by timer id."""
        if timer := self.store.timers.get(timer_id):
            device_id = get_mic_device_id_from_entity_id(self.hass, timer.entity_id)
            tm: IntentTimerManager = self.hass.data[TIMER_DATA]
            if timer := tm.timers.pop(timer_id):
                timer.cancel()
                if device_id in tm.handlers:
                    tm.handlers[device_id](TimerEventType.CANCELLED, timer)
