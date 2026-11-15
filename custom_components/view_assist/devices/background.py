"""Helper to manage background images and rotation task."""

import asyncio
from datetime import datetime as dt
import hashlib
import logging
from pathlib import Path
import random

import requests

from homeassistant.components.media_source import helper as media_source_helpers
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.util import slugify

from ..const import DEVICES, DOMAIN, IMAGE_PATH, RANDOM_IMAGE_URL  # noqa: TID252
from ..helpers import (  # noqa: TID252
    get_config_entry_by_entity_id,
    get_entity_attribute,
    get_sensor_entity_from_instance,
)
from ..typed import (  # noqa: TID252
    VABackgroundMode,
    VAConfigEntry,
    VAEvent,
    VAEventType,
)
from .base import DeviceModule

_LOGGER = logging.getLogger(__name__)


class BackgroundImageManager(DeviceModule):
    """Class to manage background images and rotation tasks."""

    _dependencies = ["StatusManager"]

    @classmethod
    def get(
        cls, hass: HomeAssistant, config: VAConfigEntry
    ) -> BackgroundImageManager | None:
        """Get the instance for a config entry."""
        try:
            return hass.data[DOMAIN][DEVICES][config.entry_id][cls.__name__]
        except KeyError:
            return None

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialize the device module."""
        super().__init__(hass, config)

        self.name = config.runtime_data.core.name
        self.mode = VABackgroundMode.DEFAULT_BACKGROUND
        self.rotation_interval = 10  # Default to 10 minutes
        self.current_image_path: Path | None = None
        self._task = None

    async def async_setup(self) -> bool:
        """Load the device module."""

        # await ImageProvider.get_media_image(self._hass)  # Preload media images

        self.mode = (
            self._config.runtime_data.dashboard.background_settings.background_mode
        )
        self.rotation_interval = (
            self._config.runtime_data.dashboard.background_settings.rotate_background_interval
            or 10
        )

        if self.mode == VABackgroundMode.DEFAULT_BACKGROUND:
            image_path = Path(
                self._hass.config.config_dir,
                self._config.runtime_data.dashboard.background_settings.background,
            )
            await self._set_background_image(self._make_url_from_path(image_path))
        elif self.mode == VABackgroundMode.LINKED:
            listener_entity_id = self._config.runtime_data.dashboard.background_settings.rotate_background_linked_entity
            listener_config_entry: VAConfigEntry = get_config_entry_by_entity_id(
                self._hass, listener_entity_id
            )
            if (
                listener_config_entry
                and listener_config_entry.entry_id != self._config.entry_id
            ):
                # Get initial image from linked entity
                background = get_entity_attribute(
                    self._hass, listener_entity_id, "background"
                )
                if background:
                    await self._set_background_image(background)

                # Listen for changes to linked entity
                self._config.async_on_unload(
                    async_dispatcher_connect(
                        self._hass,
                        f"{DOMAIN}_{listener_config_entry.entry_id}_event",
                        self._handle_linked_image_change_event,
                    )
                )
                _LOGGER.debug(
                    "Listening for background image changes from %s for %s",
                    listener_entity_id,
                    self.name,
                )
            else:
                _LOGGER.warning(
                    "No valid linked entity found for background image changes for %s",
                    self.name,
                )
        else:
            # Start image rotation task
            await self._start_background_task()
        return True

    async def async_unload(self) -> bool:
        """Unload the device module."""
        if self._task:
            self._task.cancel()
            self._task = None
        return True

    async def _start_background_task(self):
        """Start the background task for rotating images."""
        if self._task is None:
            self._task = self._config.async_create_background_task(
                self._hass,
                self._async_background_image_rotation_task(),
                f"{self._config.runtime_data.core.name} rotate image task",
            )

    async def _async_background_image_rotation_task(self):
        """Rotate background images."""
        while True:
            # Logic to rotate images
            await self._update_background_image()
            await asyncio.sleep(self.rotation_interval * 60)

    async def _update_background_image(self):
        """Update the background image based on the current mode."""
        image_path = None
        source_path = self._config.runtime_data.dashboard.background_settings.rotate_background_path

        if self.mode != VABackgroundMode.DEFAULT_BACKGROUND:
            # Get next image for mode
            if self.mode == VABackgroundMode.LOCAL_SEQUENCE:
                image_path = await self._get_next_image_file_path(source_path)
            elif self.mode == VABackgroundMode.LOCAL_RANDOM:
                image_path = await self._get_next_image_file_path(
                    source_path, randomise=True
                )
            elif self.mode == VABackgroundMode.DOWNLOAD_RANDOM:
                image_path = await self._get_download_image_path(RANDOM_IMAGE_URL)
            elif self.mode == VABackgroundMode.DOWNLOAD_URL:
                image_path = await self._get_download_image_path(source_path)
            elif self.mode == VABackgroundMode.IMMICH_RANDOM:
                image_path = await self._get_immich_image_path()

        if image_path is None:
            # If error getting image, revert to default
            image_path = Path(
                self._hass.config.config_dir,
                self._config.runtime_data.dashboard.background_settings.background,
            )

        await self._set_background_image(self._make_url_from_path(image_path))

    async def _get_next_image_file_path(
        self, load_from_path: str, randomise: bool = False
    ) -> Path | None:
        """Get the next image file path based on the current mode."""
        image = await self._hass.async_add_executor_job(
            ImageProvider.get_next_image_from_path,
            self._hass,
            load_from_path,
            self.current_image_path,
            randomise,
        )
        if image:
            self.current_image_path = image

        return image

    async def _get_download_image_path(self, url: str) -> Path | None:
        """Download an image from a URL and return the file path."""
        return await self._hass.async_add_executor_job(
            ImageProvider.get_download_image,
            self._hass,
            self._config,
            url,
        )

    async def _get_immich_image_path(self) -> Path | None:
        """Get a random photo from the Immich server and return the file path."""
        return await self._hass.async_add_executor_job(
            ImageProvider.get_immich_random_image,
            self._hass,
            self._config,
        )

    async def _handle_linked_image_change_event(self, event: VAEvent | None = None):
        """Handle image change events from linked entities."""
        if event and event.event_name == VAEventType.BACKGROUND_CHANGE:
            _LOGGER.debug("Received linked background image change event: %s", event)
            if image := event.payload.get("background"):
                await self._set_background_image(image)

    def _make_url_from_path(self, path: Path) -> str:
        """Convert a Path object to a URI string."""
        try:
            image_url = (
                path.as_uri()
                .replace("file://", "")
                .replace(self._hass.config.config_dir, "")
            )
            # Add parameter to override cache
            return f"{image_url}?v={dt.now().strftime('%Y%m%d%H%M%S')}"
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error creating image url from path %s: %s", path, ex)

    async def _set_background_image(self, image_url: str) -> None:
        """Set the background image for the entity."""
        # Get sensor entity for this instance
        entity_id = get_sensor_entity_from_instance(self._hass, self._config.entry_id)

        _LOGGER.debug("Setting background image for %s to %s", entity_id, image_url)
        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self._config.entry_id}_event",
            VAEvent(VAEventType.BACKGROUND_CHANGE, {"background": image_url}),
        )


# ----------------------------------------------------------------
# Image Provider
# ----------------------------------------------------------------
class ImageProvider:
    """Class to provide images from various sources."""

    @staticmethod
    def get_file_last_modified_age(file_path: Path) -> int | None:
        """Get the age of the downloaded image in days."""

        if file_path.exists():
            modified_time = dt.fromtimestamp(file_path.stat().st_mtime)
            return (dt.now() - modified_time).total_seconds() / 60
        return None

    @staticmethod
    def get_download_image(
        hass: HomeAssistant,
        config: VAConfigEntry,
        url: str,
        save_path: str = IMAGE_PATH,
    ) -> Path:
        """Get url from url endpoint. Endpoint can be a random image provider url like unsplash."""

        if not url.startswith(("http://", "https://")):
            return None

        path = Path(hass.config.config_dir, DOMAIN, save_path)
        filename = f"downloaded_{config.entry_id.lower()}_{slugify(config.runtime_data.core.name)}.jpg"
        image = Path(path, filename)

        # Check existing download image is not expired and use it if not
        max_age = (
            config.runtime_data.dashboard.background_settings.rotate_background_interval
        )
        image_age = ImageProvider.get_file_last_modified_age(image)

        if (
            image_age is None or (image_age + 0.5) > max_age
        ):  # 30s added for download time delays
            # Download new image
            _LOGGER.debug("Downloading new background image from %s", url)
            try:
                response = requests.get(url, timeout=15)
            except TimeoutError:
                _LOGGER.warning("Timeout trying to fetch random image from %s", url)
            else:
                if response.status_code == 200:
                    try:
                        # Ensure path exists
                        path.mkdir(parents=True, exist_ok=True)
                        with image.open(mode="wb") as file:
                            file.write(response.content)
                    except OSError as ex:
                        _LOGGER.warning(
                            "Unable to save downloaded random image file.  Error is %s",
                            ex,
                        )

        if image.exists():
            return image

        _LOGGER.warning("No existing images found for background")
        return None

    @staticmethod
    def _immich_request(
        method: str, url: str, api_key: str, **kwargs
    ) -> requests.Response | None:
        """Call the Immich API. Logs failures but never logs the API key."""
        try:
            response = requests.request(
                method,
                url,
                headers={"x-api-key": api_key},
                timeout=15,
                **kwargs,
            )
        except requests.RequestException as ex:
            _LOGGER.warning("Could not reach Immich server: %s", ex)
            return None

        if response.status_code == 401:
            _LOGGER.warning("Immich rejected the API key (HTTP 401)")
            return None
        if response.status_code == 403:
            _LOGGER.warning(
                "Immich API key is missing a permission (HTTP 403). "
                "It needs asset.read, asset.view and album.read"
            )
            return None
        if not 200 <= response.status_code < 300:
            _LOGGER.warning(
                "Immich returned HTTP %s for %s", response.status_code, url
            )
            return None
        return response

    @staticmethod
    def _immich_album_id(server: str, api_key: str, album_name: str) -> str | None:
        """Look up an album ID from its exact name."""
        response = ImageProvider._immich_request(
            "GET", f"{server}/api/albums", api_key
        )
        if response is None:
            return None
        try:
            albums = response.json()
        except ValueError:
            _LOGGER.warning("Immich album list was not valid JSON")
            return None
        for album in albums:
            if album.get("albumName") == album_name:
                return album.get("id")
        _LOGGER.warning("No Immich album named '%s' was found", album_name)
        return None

    @staticmethod
    def get_immich_random_image(
        hass: HomeAssistant,
        config: VAConfigEntry,
        save_path: str = IMAGE_PATH,
    ) -> Path | None:
        """Download a random photo from Immich, optionally limited to one album."""
        bg = config.runtime_data.dashboard.background_settings
        server = (bg.immich_server_url or "").strip().rstrip("/")
        api_key = (bg.immich_api_key or "").strip()
        album_name = (bg.immich_album_name or "").strip()

        if not server.startswith(("http://", "https://")) or not api_key:
            _LOGGER.warning("Immich background needs a server URL and an API key")
            return None

        path = Path(hass.config.config_dir, DOMAIN, save_path)
        # Key the cache on server and album, so changing either fetches a new image
        cache_key = hashlib.md5(f"{server}|{album_name}".encode()).hexdigest()[:8]
        filename = (
            f"downloaded_{config.entry_id.lower()}_{slugify(config.runtime_data.core.name)}"
            f"_immich_{cache_key}.jpg"
        )
        image = Path(path, filename)

        # Reuse the saved image while it is still within the rotation interval
        max_age = bg.rotate_background_interval or 10
        image_age = ImageProvider.get_file_last_modified_age(image)
        if image_age is not None and (image_age + 0.5) <= max_age:
            _LOGGER.debug(
                "Reusing Immich background %s (%.1f minutes old)", image.name, image_age
            )
            return image

        _LOGGER.debug("Fetching new random background from Immich")

        search: dict = {"size": 1, "type": "IMAGE"}
        if album_name:
            album_id = ImageProvider._immich_album_id(server, api_key, album_name)
            if album_id is None:
                # Wrong album name: show the default background rather than a stale photo
                return None
            _LOGGER.debug("Immich album '%s' resolved to %s", album_name, album_id)
            search["albumIds"] = [album_id]
        else:
            _LOGGER.debug("Immich background using the whole library")

        # Step 1: pick a random asset
        response = ImageProvider._immich_request(
            "POST",
            f"{server}/api/search/random",
            api_key,
            json=search,
        )
        assets = response.json() if response is not None else []
        if not assets:
            _LOGGER.warning("Immich returned no images for the background")
            return image if image.exists() else None
        asset_id = assets[0].get("id")
        _LOGGER.info(
            "Immich background asset %s selected (album: %s)",
            asset_id,
            album_name or "whole library",
        )

        # Step 2: download its preview image
        response = ImageProvider._immich_request(
            "GET",
            f"{server}/api/assets/{asset_id}/thumbnail",
            api_key,
            params={"size": "preview"},
        )
        if response is None:
            return image if image.exists() else None

        try:
            path.mkdir(parents=True, exist_ok=True)
            with image.open(mode="wb") as file:
                file.write(response.content)
        except OSError as ex:
            _LOGGER.warning("Unable to save Immich background image: %s", ex)
            return image if image.exists() else None

        return image

    @staticmethod
    def get_next_image_from_path(
        hass: HomeAssistant,
        path: str,
        current_image: str | None = None,
        randomise: bool = False,
    ) -> Path | None:
        """Get the next image file path based on the current mode."""
        valid_extensions = (".jpeg", ".jpg", ".tif", ".png")

        if path.startswith(("http://", "https://")):
            return None

        try:
            # Get path under config/view_assist directory
            dir_path = Path(
                hass.config.config_dir, DOMAIN, path.removeprefix("/").removesuffix("/")
            )
            if not dir_path.exists():
                _LOGGER.warning("File image path %s does not exist", dir_path)
                return None

            image_list = [
                f
                for f in dir_path.iterdir()
                if f.is_file and f.name.endswith(valid_extensions)
            ]

            # Check if any images were found
            if not image_list:
                _LOGGER.warning("No images found in image path - %s", dir_path)
                return None

            # Random image from list
            if randomise:
                return Path(random.choice(image_list))

            # Logic to get the next image in sequence
            if current_image is None:
                return Path(image_list[0])

            current_index = image_list.index(current_image)
            next_index = (current_index + 1) % len(image_list)
            return Path(image_list[next_index])
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error accessing path %s: %s", path, ex)

    @staticmethod
    async def get_media_image(
        hass: HomeAssistant, save_path: str = IMAGE_PATH
    ) -> Path | None:
        """Get image from media."""
        # results = media_source_helpers.async_search_media(hass, query=query)
        media = await media_source_helpers.async_browse_media(
            hass, "media-source://image"
        )
        _LOGGER.debug("Media search results: %s", media.as_dict())
