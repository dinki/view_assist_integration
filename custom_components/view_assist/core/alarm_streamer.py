"""Alarm streamer.

Encodes an mp3/wav alarm sound to a fixed-bitrate MP3 clip once, then loops
it forever and serves it over HTTP as an MP3 stream.  Services accept a
View Assist entity; the media player associated with that View Assist
instance is resolved and the stream URL is handed to it - via the Music
Assistant `play_announcement` service when it belongs to Music Assistant,
otherwise via the standard `media_player.play_media` service with
`announce` set to True.  Start and stop events are still fired on the
event bus so other automations/devices can react to an alarm sounding or
being cancelled.
"""

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
import logging
import uuid

from aiohttp import web
import voluptuous as vol

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity, selector
from homeassistant.helpers.entity_component import DATA_INSTANCES, EntityComponent
from homeassistant.helpers.network import get_url

from ..const import (  # noqa: TID252
    ATTR_MAX_REPEATS,
    ATTR_MEDIA_FILE,
    ATTR_STREAM_URL,
    ATTR_TIMER_ID,
    BROWSERMOD_DOMAIN,
    DOMAIN,
    EVENT_ALARM_SOUND,
    EVENT_ALARM_STOP,
    MUSIC_ASSISTANT_DOMAIN,
)
from ..helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_mic_device_id_from_entity_id,
)
from .timers import TimerManager

_LOGGER = logging.getLogger(__name__)

# MP3 format used for the encoded/looped audio.  A fixed (constant) bitrate
# is required so playback position can be paced from elapsed bytes alone.
SAMPLE_RATE = 44100
CHANNELS = 2
BITRATE_KBPS = 128

# How far ahead of "now" each subscriber is kept buffered so playback can
# start immediately and rides out small hiccups without stuttering.
BUFFER_SECONDS = 3
# How often the broadcast clock ticks and pushes new audio to subscribers.
TICK_SECONDS = 0.2
# How long to allow ffmpeg to transcode the source file before giving up.
TRANSCODE_TIMEOUT = 30

ALARM_SOUND_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Required(ATTR_MEDIA_FILE): str,
        vol.Optional(ATTR_MAX_REPEATS, default=0): int,
    }
)

STOP_ALARM_SOUND_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(integration=DOMAIN)
        ),
        vol.Optional(ATTR_TIMER_ID): str,
    }
)


class AlarmAudioSource:
    """An encoded MP3 audio clip, ready to be looped and streamed."""

    def __init__(self, mp3: bytes) -> None:
        """Initialise."""
        self.mp3 = mp3
        self.bytes_per_second = BITRATE_KBPS * 1000 // 8
        self.duration = len(mp3) / self.bytes_per_second

    def read_looped(self, offset: int, length: int) -> bytes:
        """Return `length` bytes starting at `offset`, wrapping around the clip."""
        loop_len = len(self.mp3)
        offset %= loop_len
        if offset + length <= loop_len:
            return self.mp3[offset : offset + length]

        parts = [self.mp3[offset:]]
        remaining = length - len(parts[0])
        while remaining > 0:
            take = min(remaining, loop_len)
            parts.append(self.mp3[:take])
            remaining -= take
        return b"".join(parts)


class AlarmBroadcast:
    """Loops an encoded audio clip and fans it out to any number of HTTP subscribers."""

    def __init__(self, source: AlarmAudioSource) -> None:
        """Initialise."""
        self.source = source
        self._subscribers: set[asyncio.Queue[bytes | None]] = set()
        self._lead_in: deque[bytes] = deque()
        self._lead_in_bytes = 0
        self._task: asyncio.Task | None = None

    def start(self, task: asyncio.Task) -> None:
        """Attach the running pump task so it can be cancelled on stop."""
        self._task = task

    async def pump(self) -> None:
        """Advance the playback clock and push newly due audio to subscribers."""
        loop = asyncio.get_running_loop()
        bytes_per_second = self.source.bytes_per_second
        lead_in_cap = int(bytes_per_second * BUFFER_SECONDS)

        start = loop.time()
        sent = 0
        with suppress(asyncio.CancelledError):
            while True:
                await asyncio.sleep(TICK_SECONDS)
                elapsed = loop.time() - start
                target = int(elapsed * bytes_per_second)
                if target <= sent:
                    continue

                chunk = self.source.read_looped(sent, target - sent)
                sent = target

                self._lead_in.append(chunk)
                self._lead_in_bytes += len(chunk)
                while self._lead_in_bytes > lead_in_cap and len(self._lead_in) > 1:
                    self._lead_in_bytes -= len(self._lead_in.popleft())

                for queue in list(self._subscribers):
                    with suppress(asyncio.QueueFull):
                        queue.put_nowait(chunk)

    def subscribe(self) -> asyncio.Queue[bytes | None]:
        """Add a subscriber, seeded with the current lead-in buffer."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        for chunk in self._lead_in:
            with suppress(asyncio.QueueFull):
                queue.put_nowait(chunk)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[bytes | None]) -> None:
        """Remove a subscriber."""
        self._subscribers.discard(queue)

    def stop(self) -> None:
        """Stop looping and immediately end every subscriber's stream."""
        if self._task and not self._task.done():
            self._task.cancel()
        for queue in list(self._subscribers):
            with suppress(asyncio.QueueFull):
                queue.put_nowait(None)
        self._subscribers.clear()


class AlarmStreamView(HomeAssistantView):
    """Serve the looping MP3 stream for an in-progress alarm."""

    url = f"/api/{DOMAIN}/alarm_stream/{{stream_id}}"
    name = f"api:{DOMAIN}:alarm_stream"
    requires_auth = False

    def __init__(self, streamer: AlarmStreamer) -> None:
        """Initialise."""
        self._streamer = streamer

    async def get(self, request: web.Request, stream_id: str) -> web.StreamResponse:
        """Stream the alarm audio until the alarm is cancelled or the client disconnects."""
        broadcast = self._streamer.get_broadcast(stream_id)
        if broadcast is None:
            return web.Response(status=404)

        response = web.StreamResponse(
            status=200,
            headers={"Cache-Control": "no-cache"},
        )
        response.content_type = "audio/mpeg"
        response.enable_chunked_encoding()
        await response.prepare(request)

        queue = broadcast.subscribe()
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    # Alarm was cancelled - end the stream immediately
                    break
                await response.write(chunk)
        except ConnectionResetError, ConnectionError, asyncio.CancelledError:
            pass
        finally:
            broadcast.unsubscribe(queue)

        return response


@dataclass
class PlayingMedia:
    """What a media player was playing before an alarm interrupted it."""

    media_content_id: str
    media_type: str = "music"


@dataclass
class ActiveAlarm:
    """An alarm currently streaming to a media player."""

    stream_id: str
    broadcast: AlarmBroadcast
    media_player_entity_id: str | None = None
    device_id: str | None = None
    is_music_assistant: bool = False
    resume_media: PlayingMedia | None = None
    auto_stop_task: asyncio.Task | None = field(default=None)


class AlarmStreamer:
    """Class to sound alarms by streaming a looping audio clip to a media player."""

    @classmethod
    def get(cls, hass: HomeAssistant) -> AlarmStreamer | None:
        """Get the alarm streamer instance."""
        try:
            return hass.data[DOMAIN][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: ConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config

        self._sources: dict[str, AlarmAudioSource] = {}
        self._active: dict[str, ActiveAlarm] = {}  # keyed by entity_id
        self._streams: dict[str, str] = {}  # stream_id -> entity_id

    async def async_setup(self) -> bool:
        """Start the alarm streamer."""
        self.hass.http.register_view(AlarmStreamView(self))

        self.hass.services.async_register(
            DOMAIN,
            "sound_alarm",
            self._async_handle_alarm_sound,
            schema=ALARM_SOUND_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

        self.hass.services.async_register(
            DOMAIN,
            "cancel_sound_alarm",
            self._async_handle_stop_alarm_sound,
            schema=STOP_ALARM_SOUND_SERVICE_SCHEMA,
        )

        return True

    async def async_unload(self) -> bool:
        """Stop the alarm streamer."""
        await self.cancel_alarm_sound()
        self.hass.services.async_remove(DOMAIN, "sound_alarm")
        self.hass.services.async_remove(DOMAIN, "cancel_sound_alarm")
        return True

    def get_broadcast(self, stream_id: str) -> AlarmBroadcast | None:
        """Get the broadcast for a stream id, if it's still active."""
        entity_id = self._streams.get(stream_id)
        if entity_id and (active := self._active.get(entity_id)):
            if active.stream_id == stream_id:
                return active.broadcast
        return None

    async def _async_handle_alarm_sound(self, call: ServiceCall) -> ServiceResponse:
        """Handle alarm sound."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        media_file = call.data.get(ATTR_MEDIA_FILE)
        max_repeats = call.data.get(ATTR_MAX_REPEATS, 0)

        return await self.alarm_sound(entity_id, media_file, max_repeats)

    async def _async_handle_stop_alarm_sound(self, call: ServiceCall) -> None:
        """Handle stop alarm sound."""
        entity_id = call.data.get(ATTR_ENTITY_ID)
        timer_id = call.data.get(ATTR_TIMER_ID)
        await self.cancel_alarm_sound(entity_id, timer_id)

    def _get_entity_from_entity_id(self, entity_id: str) -> MediaPlayerEntity | None:
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
        """Capture what a media player was playing before an alarm interrupts it."""
        if media_entity.state != MediaPlayerState.PLAYING:
            return None

        if media_entity.platform.platform_name == BROWSERMOD_DOMAIN:
            data = media_entity._data  # noqa: SLF001
            if content_id := data.get("player", {}).get("src"):
                return PlayingMedia(media_content_id=content_id)
            return None

        if content_id := media_entity.media_content_id:
            return PlayingMedia(
                media_content_id=content_id,
                media_type=media_entity.media_content_type or "music",
            )
        return None

    def _resolve_media_player_entity_id(self, entity_id: str) -> str | None:
        """Resolve the media player entity for a View Assist instance's entity."""
        config_entry = get_config_entry_by_entity_id(self.hass, entity_id)
        if config_entry:
            return config_entry.runtime_data.core.mediaplayer_device
        return None

    def _resolve_media_url(self, media_file: str) -> str:
        """Resolve a media file reference to a URL ffmpeg can read from."""
        if media_file.startswith(("http://", "https://")):
            return media_file
        return f"{get_url(self.hass)}/{media_file.removeprefix('/')}"

    async def _get_or_encode_source(self, media_url: str) -> AlarmAudioSource:
        """Get a cached encoded source, transcoding it via ffmpeg if not seen before."""
        if media_url not in self._sources:
            self._sources[media_url] = await self._encode_to_mp3(media_url)
        return self._sources[media_url]

    async def _encode_to_mp3(self, media_url: str) -> AlarmAudioSource:
        """Transcode an mp3/wav file/url to a fixed-bitrate MP3 clip using ffmpeg."""
        ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            media_url,
            "-f",
            "mp3",
            "-acodec",
            "libmp3lame",
            "-b:a",
            f"{BITRATE_KBPS}k",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            str(CHANNELS),
            "-write_xing",
            "0",
            "-loglevel",
            "error",
            "pipe:1",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(TRANSCODE_TIMEOUT):
                mp3_data, stderr = await proc.communicate()
        except TimeoutError:
            proc.kill()
            raise HomeAssistantError(
                f"Timed out encoding alarm media {media_url}"
            ) from None

        if proc.returncode != 0 or not mp3_data:
            raise HomeAssistantError(
                f"Failed to encode alarm media {media_url}: "
                f"{stderr.decode(errors='ignore').strip()}"
            )

        return AlarmAudioSource(mp3_data)

    async def alarm_sound(
        self,
        entity_id: str,
        media_file: str,
        max_repeats: int = 0,
    ) -> ServiceResponse:
        """Start streaming a looping alarm sound via the View Assist entity's media player."""
        _LOGGER.debug(
            "Alarm sound called for %s. Alarm file: %s. Repeats: %s",
            entity_id,
            media_file,
            max_repeats,
        )

        if entity_id in self._active:
            _LOGGER.warning(
                "Alarm already in progress on %s.  Ignoring this request", entity_id
            )
            return None

        # Fix for custom sentences in VA using media player entity instead of vaca entity for alarm sound service call.
        if entity_id.split(".", 1)[0] != "media_player":
            media_player_entity_id = self._resolve_media_player_entity_id(entity_id)
            if not media_player_entity_id:
                _LOGGER.error(
                    "No media player configured for View Assist entity %s", entity_id
                )
                return None
        else:
            media_player_entity_id = entity_id

        media_entity = self._get_entity_from_entity_id(media_player_entity_id)
        if not media_entity:
            _LOGGER.error(
                "Invalid media player entity. %s not found", media_player_entity_id
            )
            return None

        # Music Assistant announcements resume prior playback natively; for
        # everything else, capture what's playing now so it can be resumed
        # once the alarm stops.
        is_music_assistant = (
            media_entity.platform.platform_name == MUSIC_ASSISTANT_DOMAIN
        )
        resume_media = (
            None
            if is_music_assistant
            else self._get_currently_playing_media(media_entity)
        )

        media_url = self._resolve_media_url(media_file)
        try:
            source = await self._get_or_encode_source(media_url)
        except HomeAssistantError as ex:
            _LOGGER.error("Unable to encode alarm media %s: %s", media_url, ex)
            return None

        stream_id = uuid.uuid4().hex
        broadcast = AlarmBroadcast(source)
        broadcast.start(
            self.config.async_create_background_task(
                self.hass,
                broadcast.pump(),
                name=f"AlarmStreamPump-{stream_id}",
            )
        )

        device_id = get_mic_device_id_from_entity_id(self.hass, entity_id)
        active = ActiveAlarm(
            stream_id=stream_id,
            broadcast=broadcast,
            media_player_entity_id=media_player_entity_id,
            device_id=device_id,
            is_music_assistant=is_music_assistant,
            resume_media=resume_media,
        )
        if max_repeats:
            active.auto_stop_task = self.config.async_create_background_task(
                self.hass,
                self._auto_stop_after(entity_id, source.duration * max_repeats),
                name=f"AlarmStreamAutoStop-{stream_id}",
            )

        self._active[entity_id] = active
        self._streams[stream_id] = entity_id

        url = f"{get_url(self.hass)}{AlarmStreamView.url.format(stream_id=stream_id)}"
        _LOGGER.debug("Alarm sound started for %s. Stream url: %s", entity_id, url)

        self.hass.bus.async_fire(
            EVENT_ALARM_SOUND, {ATTR_DEVICE_ID: device_id, ATTR_STREAM_URL: url}
        )

        try:
            if is_music_assistant:
                _LOGGER.debug(
                    "Playing alarm on %s via Music Assistant announcement", entity_id
                )
                await self.hass.services.async_call(
                    "music_assistant",
                    "play_announcement",
                    service_data={"url": url},
                    target={"entity_id": media_player_entity_id},
                )
            else:
                _LOGGER.debug(
                    "Playing alarm on %s via media_player.play_media", entity_id
                )
                await self.hass.services.async_call(
                    "media_player",
                    "play_media",
                    service_data={
                        "media_content_id": url,
                        "media_content_type": MediaType.MUSIC,
                        "announce": True,
                    },
                    target={"entity_id": media_player_entity_id},
                )
        except HomeAssistantError as ex:
            _LOGGER.error("Unable to play alarm sound on %s: %s", entity_id, ex)
            await self.cancel_alarm_sound(entity_id)
            return None

        return {ATTR_STREAM_URL: url}

    async def _auto_stop_after(self, entity_id: str, delay: float) -> None:
        """Stop an alarm automatically once its repeat count has elapsed."""
        await asyncio.sleep(delay)
        await self.cancel_alarm_sound(entity_id)

    async def cancel_alarm_sound(
        self, entity_id: str | None = None, timer_id: str | None = None
    ) -> None:
        """Cancel a streaming alarm and stop it playing on the media player."""
        entity_ids = [entity_id] if entity_id else list(self._active)

        for ent_id in entity_ids:
            active = self._active.pop(ent_id, None)
            if not active:
                continue

            self._streams.pop(active.stream_id, None)
            if active.auto_stop_task and not active.auto_stop_task.done():
                active.auto_stop_task.cancel()

            # Stops the loop and immediately ends the stream being fed to the player
            active.broadcast.stop()

            # Music Assistant resumes prior playback natively once its
            # announcement stops - nothing further to do for that target.
            if active.media_player_entity_id and not active.is_music_assistant:
                if active.resume_media:
                    _LOGGER.debug(
                        "Resuming previous media on %s: %s",
                        active.media_player_entity_id,
                        active.resume_media,
                    )
                    try:
                        await self.hass.services.async_call(
                            "media_player",
                            "play_media",
                            {
                                "entity_id": active.media_player_entity_id,
                                "media_content_type": active.resume_media.media_type,
                                "media_content_id": active.resume_media.media_content_id,
                            },
                        )
                    except HomeAssistantError as ex:
                        _LOGGER.debug(
                            "Unable to resume previous media on %s: %s",
                            active.media_player_entity_id,
                            ex,
                        )
                else:
                    try:
                        await self.hass.services.async_call(
                            "media_player",
                            "media_stop",
                            target={"entity_id": active.media_player_entity_id},
                        )
                    except HomeAssistantError as ex:
                        _LOGGER.debug(
                            "Unable to stop media player %s: %s",
                            active.media_player_entity_id,
                            ex,
                        )

            _LOGGER.debug("Alarm sound cancelled for %s", ent_id)
            self.hass.bus.async_fire(
                EVENT_ALARM_STOP, {ATTR_DEVICE_ID: active.device_id}
            )

        # Cancel timer if timer_id provided
        if timer_id:
            tm = TimerManager.get(self.hass)
            if tm:
                await tm.cancel_timer(timer_id)
                _LOGGER.debug("Cancelled timer %s", timer_id)
