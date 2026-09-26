"""Handlers alarm repeater."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import io
import logging
import time
from typing import Any

import mutagen
import requests
import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.helpers import entity, selector
from homeassistant.helpers.entity_component import DATA_INSTANCES, EntityComponent
from homeassistant.helpers.network import get_url

from ..const import (  # noqa: TID252
    ATTR_MAX_REPEATS,
    ATTR_MEDIA_FILE,
    ATTR_RESUME_MEDIA,
    ATTR_TIMER_ID,
    BROWSERMOD_DOMAIN,
    DOMAIN,
)
from .timers import TimerManager

_LOGGER = logging.getLogger(__name__)

ALARMS = "alarms"

ALARM_SOUND_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Required(ATTR_MEDIA_FILE): str,
        vol.Optional(ATTR_RESUME_MEDIA, default=True): bool,
        vol.Optional(ATTR_MAX_REPEATS, default=0): int,
    }
)

STOP_ALARM_SOUND_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Optional(ATTR_TIMER_ID): str,
    }
)


@dataclass
class PlayingMedia:
    """Class to hold paused media."""

    media_content_id: str
    media_type: str = "music"
    media_position: float = 0
    volume: int = 0
    player: Any | None = None
    queue: Any | None = None


class AlarmRepeater:
    """Class to handle announcing on media player with resume."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> AlarmRepeater | None:
        """Get the alarm repeater instance."""
        return hass.data[DOMAIN][cls.__name__]

    def __init__(self, hass: HomeAssistant, config: ConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config

        self.alarm_tasks: dict[str, asyncio.Task] = {}

        self.announcement_in_progress: bool = False

    async def async_setup(self) -> bool:
        """Start the alarm repeater."""

        self.hass.services.async_register(
            DOMAIN,
            "sound_alarm",
            self._async_handle_alarm_sound,
            schema=ALARM_SOUND_SERVICE_SCHEMA,
        )

        self.hass.services.async_register(
            DOMAIN,
            "cancel_sound_alarm",
            self._async_handle_stop_alarm_sound,
            schema=STOP_ALARM_SOUND_SERVICE_SCHEMA,
        )

        return True

    async def async_unload(self) -> bool:
        """Stop the alarm repeater."""
        await self.cancel_alarm_sound()
        self.hass.services.async_remove(DOMAIN, "sound_alarm")
        self.hass.services.async_remove(DOMAIN, "cancel_sound_alarm")
        return True

    async def _async_handle_alarm_sound(self, call: ServiceCall) -> ServiceResponse:
        """Handle alarm sound."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        media_file = call.data.get(ATTR_MEDIA_FILE)
        resume_media = call.data.get(ATTR_RESUME_MEDIA)
        max_repeats = call.data.get(ATTR_MAX_REPEATS)

        return await self.alarm_sound(
            entity_id, media_file, "music", resume_media, max_repeats
        )

    async def _async_handle_stop_alarm_sound(self, call: ServiceCall):
        """Handle stop alarm sound."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        timer_id = call.data.get(ATTR_TIMER_ID)
        await self.cancel_alarm_sound(entity_id, timer_id)

    def _get_entity_from_entity_id(self, entity_id: str):
        """Get entity object from entity_id."""
        domain = entity_id.partition(".")[0]
        entity_comp: EntityComponent[entity.Entity] | None
        entity_comp = self.hass.data.get(DATA_INSTANCES, {}).get(domain)
        if entity_comp:
            return entity_comp.get_entity(entity_id)
        return None

    def _get_currently_playing_media(
        self, media_entity: MediaPlayerEntity
    ) -> PlayingMedia | None:
        """Try and get currently playing media."""

        # If not playing then return None
        if media_entity.state != MediaPlayerState.PLAYING:
            _LOGGER.debug(
                "%s is in %s state - will not attempt restore",
                media_entity.entity_id,
                media_entity.state,
            )
            return None

        data = None

        # Hook into integration data to get currently playing media
        media_integration = media_entity.platform.platform_name

        # Browermod
        if media_integration == BROWSERMOD_DOMAIN:
            _data = media_entity._data  # noqa: SLF001
            _LOGGER.debug("BrowserMod Data: %s", _data)
            data = PlayingMedia(
                media_content_id=_data["player"].get("src"),
                media_type="music",
                media_position=_data.get("player", {}).get("media_position", None),
                volume=_data.get("player", {}).get("volume", 0),
            )
        # An integration may populate these properties - most don't seem to
        elif content_id := media_entity.media_content_id:
            data = PlayingMedia(
                media_content_id=content_id,
                media_type=media_entity.media_content_type or "music",
                media_position=media_entity.media_position,
            )

        _LOGGER.debug("Current playing media: %s", data)
        return data

    def _get_file_info(self, media_url: str):
        resp = requests.get(media_url, stream=True, timeout=10)
        metadata = mutagen.File(io.BytesIO(resp.content))
        return round(float(metadata.info.length), 2)

    def _media_player_supports_announce(self, media_player: MediaPlayerEntity) -> bool:
        return (
            MediaPlayerEntityFeature.MEDIA_ANNOUNCE in media_player.supported_features
        )

    async def wait_for_state(
        self,
        media_entity: MediaPlayerEntity,
        data_var: str,
        attribute: str,
        wanted_state: Any,
        timeout: float = 60.0,
    ) -> None:
        """Wait for an object attribute to reach the given state."""
        start_timestamp = time.time()
        _LOGGER.debug("Waiting for %s to reach state %s", attribute, wanted_state)
        try:
            async with asyncio.timeout(timeout):
                while (
                    getattr(getattr(media_entity, data_var), attribute) != wanted_state
                ):
                    await asyncio.sleep(0.2)

        except TimeoutError:
            _LOGGER.debug(
                "%s did not reach state %s within the timeout of %s seconds",
                attribute,
                wanted_state,
                timeout,
            )
            return False
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("ERROR: %s", ex)
            return False
        elapsed_time = round(time.time() - start_timestamp, 2)
        _LOGGER.debug(
            "%s reached state %s within %s seconds",
            attribute,
            wanted_state,
            elapsed_time,
        )
        return True

    async def repeat_announce(
        self,
        media_entity: MediaPlayerEntity,
        media_type: str,
        media_url: str,
        max_repeats: int = 0,
    ):
        """Repeat announcement using announce methods."""

        duration = await self.hass.async_add_executor_job(
            self._get_file_info, media_url
        )

        _LOGGER.debug("Alarm file duration: %s", duration)

        i = 1
        is_playing = media_entity.state == MediaPlayerState.PLAYING
        _LOGGER.debug("Media state: %s", media_entity.state)
        while i <= max_repeats or max_repeats == 0:
            try:
                self.announcement_in_progress = True
                if (
                    media_entity.platform.platform_name == "music_assistant"
                    and is_playing
                ):
                    # Use native service for MASS for better performance
                    _LOGGER.debug(
                        "Announce %s, using native Music Assistant announcement service",
                        i,
                    )
                    response = await self.hass.services.async_call(
                        "music_assistant",
                        "play_announcement",
                        service_data={"url": media_url},
                        target={"entity_id": media_entity.entity_id},
                    )
                    # Wait for time for status to update
                    await asyncio.sleep(1)
                    response = await self.wait_for_state(
                        media_entity,
                        "player",
                        "announcement_in_progress",
                        False,
                        timeout=int(duration + 15),
                    )
                    # If wait for state timed out or errored, exit
                    if not response:
                        return

                    # Added to try and keep playing media position
                    await asyncio.sleep(0.5)
                else:
                    _LOGGER.debug("Announce %s, using standard play_media service", i)
                    response = await self.hass.services.async_call(
                        "media_player",
                        "play_media",
                        service_data={
                            "media_content_id": media_url,
                            "media_content_type": media_type,
                            "announce": True,
                        },
                        target={"entity_id": media_entity.entity_id},
                    )
                    _LOGGER.debug("Waiting for %ss before next iteration", duration + 1)
                    await asyncio.sleep(duration + 1)
                self.announcement_in_progress = False
            except Exception as ex:  # noqa: BLE001
                _LOGGER.error("ERROR: %s", ex)
                return
            else:
                i += 1
        _LOGGER.debug("Announce ended")

    async def repeat_media(
        self,
        media_entity: MediaPlayerEntity,
        media_type: str,
        media_url: str,
        max_repeats: int = 0,
    ):
        """Repeat playing media file."""
        i = 1
        try:
            duration = await self.hass.async_add_executor_job(
                self._get_file_info, media_url
            )
            while i <= max_repeats or not max_repeats:
                await self.hass.services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": media_entity.entity_id,
                        "media_content_type": media_type,
                        "media_content_id": media_url,
                    },
                )
                await asyncio.sleep(duration)
                i += 1
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("ERROR: %s", ex)
            return

    async def alarm_sound(
        self,
        entity_id: str,
        media_url: str,
        media_type: MediaType = MediaType.MUSIC,
        resume: bool = True,
        max_repeats: int = 50,
    ):
        """Announce to media player and resume."""

        _LOGGER.debug(
            "Alarm sound called for %s. Alarm url: %s.  Repeats: %s",
            entity_id,
            media_url,
            max_repeats,
        )
        if self.alarm_tasks.get(entity_id) and not self.alarm_tasks[entity_id].done():
            # Alarm already in progress on this device
            _LOGGER.warning(
                "Alarm already in progress on %s.  Ignoring this request", entity_id
            )
            return None

        if not media_url.startswith("http://") and not media_url.startswith("https://"):
            media_url = media_url.removeprefix("/")
            media_url = f"{get_url(self.hass)}/{media_url}"

        # Get instance of media player
        media_entity: MediaPlayerEntity
        media_entity = self._get_entity_from_entity_id(entity_id)

        if not media_entity:
            _LOGGER.error("Invalid media player entity. %s not found", entity_id)
            return None

        media_integration = media_entity.platform.platform_name
        _LOGGER.debug("Media player integration: %s", media_integration)

        if not media_entity.state or media_entity.state in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            _LOGGER.warning(
                "%s is in a state that cannot play alarm.  State is %s",
                media_entity.entity_id,
                media_entity.state,
            )
            return None

        _LOGGER.debug("Media player state: %s", media_entity.state)

        if self._media_player_supports_announce(media_entity):
            _LOGGER.debug("Media player supports announce")
            self.alarm_tasks[entity_id] = self.config.async_create_background_task(
                self.hass,
                self.repeat_announce(media_entity, media_type, media_url, max_repeats),
                name=f"AnnounceRepeat-{entity_id}",
            )

        else:
            _LOGGER.debug(
                "Media player does not support announce.  Using repeat with attempted restore"
            )
            playing_media = self._get_currently_playing_media(media_entity)

            # Sound alarm
            self.alarm_tasks[entity_id] = self.config.async_create_background_task(
                self.hass,
                self.repeat_media(media_entity, media_type, media_url, max_repeats),
                name=f"AlarmRepeat-{entity_id}",
            )

            # Wait for alarm media task to finish with max of 60s
            with asyncio.timeout(60):
                try:
                    while not self.alarm_tasks[entity_id].done():
                        await asyncio.sleep(0.5)

                    if resume and playing_media:
                        _LOGGER.debug("Resuming media: %s", playing_media)
                        await asyncio.sleep(1)
                        await self.hass.services.async_call(
                            "media_player",
                            "play_media",
                            {
                                "entity_id": entity_id,
                                "media_content_type": playing_media.media_type,
                                "media_content_id": playing_media.media_content_id,
                            },
                        )

                except asyncio.CancelledError:
                    # If we are here, something went wrong so just end
                    pass
        return {}

    async def cancel_alarm_sound(
        self, entity_id: str | None = None, timer_id: str | None = None
    ):
        """Cancel announcement."""
        if entity_id:
            entities = [entity_id]
        else:
            entities = list(self.alarm_tasks.keys())

        for mp_entity_id in entities:
            if (
                self.alarm_tasks.get(mp_entity_id)
                and not self.alarm_tasks[mp_entity_id].done()
            ):
                media_entity: MediaPlayerEntity
                media_entity = self._get_entity_from_entity_id(mp_entity_id)
                if not self._media_player_supports_announce(media_entity):
                    await self.hass.services.async_call(
                        "media_player", "media_stop", {"entity_id": mp_entity_id}
                    )
                self.alarm_tasks[mp_entity_id].cancel()
                _LOGGER.debug("Alarm sound cancelled")

        # Cancel timer if timer_id provided
        if timer_id:
            tm = TimerManager.get(self.hass)
            if tm:
                await tm.cancel_timer(timer_id)
                _LOGGER.debug("Cancelled timer %s", timer_id)
