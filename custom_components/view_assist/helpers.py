"""Helper functions."""

import os
import random
from typing import Any

import requests
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import datetime

from .const import CONF_BROWSER_ID, DOMAIN, VAConfigEntry, VAType


def is_first_instance(
    hass: HomeAssistant, config: VAConfigEntry, display_instance_only: bool = False
):
    """Return if first config entry.

    Optional to return if first config entry for instance with type of view_audio
    """
    accepted_types = [VAType.VIEW_AUDIO]
    if not display_instance_only:
        accepted_types.append(VAType.AUDIO_ONLY)

    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data["type"] in accepted_types and not entry.disabled_by
    ]

    # If first instance matches this entry id, return True
    if entries and entries[0].entry_id == config.entry_id:
        return True
    return False


def get_loaded_instance_count(hass: HomeAssistant) -> int:
    """Return number of loaded instances."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if not entry.disabled_by
    ]
    return len(entries)


def ensure_list(value: str | list[str]):
    """Ensure that a value is a list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        value = (value.replace("[", "").replace("]", "").replace('"', "")).split(",")
        return value if value else []
    return []


def get_entity_id_by_browser_id(hass: HomeAssistant, browser_id: str) -> str:
    """Get entity id form browser id.

    Support websocket
    """
    entity_registry = er.async_get(hass)

    # Get all instances of view assist
    entry_ids = [entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)]

    # For each instance, get list of entities
    for entry_id in entry_ids:
        integration_entities = er.async_entries_for_config_entry(
            entity_registry, entry_id
        )
        # Get entity ids
        entity_ids = [entity.entity_id for entity in integration_entities if entity]

        # Get entity id state and check browser id attribute
        for entity_id in entity_ids:
            if state := hass.states.get(entity_id):
                if (
                    state.attributes.get(CONF_BROWSER_ID)
                    and state.attributes[CONF_BROWSER_ID] == browser_id
                ):
                    return entity_id
    return None


# ----------------------------------------------------------------
# Images
# ----------------------------------------------------------------
def get_random_image(
    hass: HomeAssistant, directory: str, source: str
) -> dict[str, Any]:
    """Return a random image from supplied directory or url."""

    valid_extensions = (".jpeg", ".jpg", ".tif", ".png")

    if source == "local":
        config_dir = hass.config.config_dir
        # Translate /local/ to /config/www/ for directory validation
        if "local" in directory:
            filesystem_directory = directory.replace("local", f"{config_dir}/www/", 1)
        elif "config" in directory:
            filesystem_directory = directory.replace("config", f"{config_dir}/")
        else:
            filesystem_directory = f"{config_dir}/{directory}"

        # Remove any //
        filesystem_directory = filesystem_directory.replace("//", "/")

        # Verify the directory exists
        if not os.path.isdir(filesystem_directory):
            return {"error": f"The directory '{filesystem_directory}' does not exist."}

        # List only image files with the valid extensions
        dir_files = os.listdir(filesystem_directory)
        images = [f for f in dir_files if f.lower().endswith(valid_extensions)]

        # Check if any images were found
        if not images:
            return {
                "error": f"No images found in the directory '{filesystem_directory}'."
            }

        # Select a random image
        selected_image = random.choice(images)

        # Replace /config/www/ with /local/ for constructing the relative path
        if filesystem_directory.startswith(f"{config_dir}/www/"):
            relative_path = filesystem_directory.replace(
                f"{config_dir}/www/", "/local/"
            )
        else:
            relative_path = directory

        # Ensure trailing slash in the relative path
        if not relative_path.endswith("/"):
            relative_path += "/"

        # Construct the image path
        image_path = f"{relative_path}{selected_image}"

        # Remove any //
        image_path = image_path.replace("//", "/")

    elif source == "download":
        # TODO: Prevent blocking loop with requests
        url = "https://unsplash.it/640/425?random"
        response = requests.get(url)

        if response.status_code == 200:
            current_time = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"random_{current_time}.jpg"
            full_path = os.path.join(directory, filename)

            with open(full_path, "wb") as file:
                file.write(response.content)

            # Remove previous background image
            for file in os.listdir(directory):
                if file.startswith("random_") and file != filename:
                    os.remove(os.path.join(directory, file))

            image_path = full_path
        else:
            # Return existing image if the download fails
            existing_files = [
                os.path.join(directory, file)
                for file in os.listdir(directory)
                if file.startswith("random_")
            ]
            image_path = existing_files[0] if existing_files else None

        if not image_path:
            return {
                "error": "Failed to download a new image and no existing images found."
            }

    else:
        return {"error": "Invalid source specified. Use 'local' or 'download'."}

    # Return the image path in a dictionary
    return {"image_path": image_path}
