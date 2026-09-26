"""Helper to manage background images and rotation task."""

from __future__ import annotations

import asyncio
from datetime import datetime as dt
import logging
from pathlib import Path
import random

import requests

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

_LOGGER = logging.getLogger(__name__)


class BackgroundImageManager:
    """Class to manage background images and rotation tasks."""

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
        self.hass = hass
        self.config = config
        self.name = config.runtime_data.core.name

        self.mode = VABackgroundMode.DEFAULT_BACKGROUND
        self.rotation_interval = 10  # Default to 10 minutes
        self.current_image_path: Path | None = None
        self._task = None

    async def async_setup(self) -> bool:
        """Load the device module."""

        self.mode = (
            self.config.runtime_data.dashboard.background_settings.background_mode
        )
        self.rotation_interval = (
            self.config.runtime_data.dashboard.background_settings.rotate_background_interval
            or 10
        )

        if self.mode == VABackgroundMode.DEFAULT_BACKGROUND:
            image_path = Path(
                self.hass.config.config_dir,
                self.config.runtime_data.dashboard.background_settings.background,
            )
            await self._set_background_image(self._make_url_from_path(image_path))
        elif self.mode == VABackgroundMode.LINKED:
            listener_entity_id = self.config.runtime_data.dashboard.background_settings.rotate_background_linked_entity
            listener_config_entry: VAConfigEntry = get_config_entry_by_entity_id(
                self.hass, listener_entity_id
            )
            if (
                listener_config_entry
                and listener_config_entry.entry_id != self.config.entry_id
            ):
                # Get initial image from linked entity
                background = get_entity_attribute(
                    self.hass, listener_entity_id, "background"
                )
                if background:
                    await self._set_background_image(background)

                # Listen for changes to linked entity
                self.config.async_on_unload(
                    async_dispatcher_connect(
                        self.hass,
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
            self._task = self.config.async_create_background_task(
                self.hass,
                self._async_background_image_rotation_task(),
                f"{self.config.runtime_data.core.name} rotate image task",
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
        source_path = self.config.runtime_data.dashboard.background_settings.rotate_background_path

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

        if image_path is None:
            # If error getting image, revert to default
            image_path = Path(
                self.hass.config.config_dir,
                self.config.runtime_data.dashboard.background_settings.background,
            )

        await self._set_background_image(self._make_url_from_path(image_path))

    async def _get_next_image_file_path(
        self, load_from_path: str, randomise: bool = False
    ) -> Path | None:
        """Get the next image file path based on the current mode."""
        image = await self.hass.async_add_executor_job(
            ImageProvider.get_next_image_from_path,
            self.hass,
            load_from_path,
            self.current_image_path,
            randomise,
        )
        if image:
            self.current_image_path = image

        return image

    async def _get_download_image_path(self, url: str) -> Path | None:
        """Download an image from a URL and return the file path."""
        return await self.hass.async_add_executor_job(
            ImageProvider.get_download_image,
            self.hass,
            self.config,
            url,
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
                .replace(self.hass.config.config_dir, "")
            )
            # Add parameter to override cache
            return f"{image_url}?v={dt.now().strftime('%Y%m%d%H%M%S')}"
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error creating image url from path %s: %s", path, ex)

    async def _set_background_image(self, image_url: str) -> None:
        """Set the background image for the entity."""
        # Get sensor entity for this instance
        entity_id = get_sensor_entity_from_instance(self.hass, self.config.entry_id)

        _LOGGER.debug("Setting background image for %s to %s", entity_id, image_url)

        self.config.runtime_data.dashboard.background_settings.background = image_url

        async_dispatcher_send(
            self.hass,
            f"{DOMAIN}_{self.config.entry_id}_event",
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
