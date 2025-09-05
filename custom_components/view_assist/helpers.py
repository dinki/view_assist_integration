"""Helper functions."""

from functools import reduce
import json
import logging
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
import requests

from homeassistant.const import CONF_TYPE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

from .const import (
    BROWSERMOD_DOMAIN,
    CONF_DISPLAY_DEVICE,
    DASHBOARD_DIR,
    DOMAIN,
    HASSMIC_DOMAIN,
    IMAGE_PATH,
    OVERLAY_FILE_NAME,
    RANDOM_IMAGE_URL,
    REMOTE_ASSIST_DISPLAY_DOMAIN,
    VAMODE_REVERTS,
    VAMode,
)
from .typed import VAConfigEntry, VADisplayType, VAType

_LOGGER = logging.getLogger(__name__)


def get_integration_entries(
    hass: HomeAssistant,
    accepted_types: list[VAType] | None = None,
) -> list[VAConfigEntry]:
    """Get list of config entries for the integration."""
    if accepted_types is None:
        accepted_types = [VAType.VIEW_AUDIO, VAType.AUDIO_ONLY]
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data[CONF_TYPE] in accepted_types and not entry.disabled_by
    ]


def get_entity_list(
    hass: HomeAssistant,
    integration: str | list[str] | None = None,
    domain: str | list[str] | None = None,
    append: str | list[str] | None = None,
) -> list[str]:
    """Get the entity ids of devices not in dnd mode."""
    if append:
        matched_entities = ensure_list(append)
    else:
        matched_entities = []
    # Stop full list of entities returning
    if not integration and not domain:
        return matched_entities

    if domain and isinstance(domain, str):
        domain = [domain]

    if integration and isinstance(integration, str):
        integration = [integration]

    entity_registry = er.async_get(hass)
    for entity_info, entity_id in entity_registry.entities._index.items():  # noqa: SLF001
        if integration and entity_info[1] not in integration:
            continue
        if domain and entity_info[0] not in domain:
            continue
        matched_entities.append(entity_id)
    return matched_entities


def is_first_instance(
    hass: HomeAssistant, config: VAConfigEntry, display_instance_only: bool = False
):
    """Return if first config entry.

    Optional to return if first config entry for instance with type of view_audio
    """
    accepted_types = [VAType.VIEW_AUDIO]
    if not display_instance_only:
        accepted_types.append(VAType.AUDIO_ONLY)

    entries = get_integration_entries(hass, accepted_types)

    # If first instance matches this entry id, return True
    if entries and entries[0].entry_id == config.entry_id:
        return True
    return False


def ensure_list(value: str | list[str]):
    """Ensure that a value is a list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        value = (value.replace("[", "").replace("]", "").replace('"', "")).split(",")
        return value if value else []
    return []


def normalize_status_items(raw_input: Any) -> str | list[str] | None:
    """Normalize and validate status item input.

    Handles various input formats:
    - Single string
    - List of strings
    - JSON string representing a list
    - Dictionary with attributes

    Returns:
    - Single string
    - List of strings
    - None if invalid input
    """
    if raw_input is None:
        return None

    if isinstance(raw_input, str):
        if raw_input.startswith("[") and raw_input.endswith("]"):
            try:
                parsed = json.loads(raw_input)
                if isinstance(parsed, list):
                    string_items = [str(item) for item in parsed if item]
                    return string_items if string_items else None
            except json.JSONDecodeError:
                return raw_input if raw_input else None
            else:
                return None
        return raw_input if raw_input else None

    if isinstance(raw_input, list):
        string_items = [str(item) for item in raw_input if item]
        return string_items if string_items else None

    if isinstance(raw_input, dict):
        if "id" in raw_input:
            return str(raw_input["id"])
        if "name" in raw_input:
            return str(raw_input["name"])
        if "value" in raw_input:
            return str(raw_input["value"])

    return None


def get_entity_attribute(hass: HomeAssistant, entity_id: str, attribute: str) -> Any:
    """Get attribute from entity by entity_id."""
    if entity := hass.states.get(entity_id):
        return entity.attributes.get(attribute)
    return None


def get_config_entry_by_config_data_value(
    hass: HomeAssistant, value: str
) -> VAConfigEntry:
    """Get config entry from a config data param value."""
    # Loop config entries
    for entry in get_integration_entries(hass):
        for param_value in entry.data.values():
            if (
                param_value == value
                or get_device_id_from_entity_id(hass, param_value) == value
            ):
                return entry
    return None


def get_config_entry_by_entity_id(hass: HomeAssistant, entity_id: str) -> VAConfigEntry:
    """Get config entry by entity id."""
    entity_registry = er.async_get(hass)
    if entity := entity_registry.async_get(entity_id):
        return hass.config_entries.async_get_entry(entity.config_entry_id)
    return None


def get_master_config_entry(hass: HomeAssistant) -> VAConfigEntry:
    """Get master config entry."""
    if entries := get_integration_entries(hass, VAType.MASTER_CONFIG):
        return entries[0]
    return None


def get_device_name_from_id(hass: HomeAssistant, device_id: str) -> str:
    """Get the browser_id for the device based on device domain."""
    if device_id.startswith("va-"):
        return device_id
    device_reg = dr.async_get(hass)
    device = device_reg.async_get(device_id)

    return device.name if device else None


def get_device_id_from_entity_id(hass: HomeAssistant, entity_id: str) -> str:
    """Get the device id of an entity by id."""
    entity_registry = er.async_get(hass)
    if entity := entity_registry.async_get(entity_id):
        return entity.device_id
    return None


def get_devices_for_domain(hass: HomeAssistant, domain: str) -> list[dr.DeviceEntry]:
    """Get all devices for a domain."""
    device_reg = dr.async_get(hass)
    entries = list(
        hass.config_entries.async_entries(
            domain, include_ignore=False, include_disabled=False
        )
    )

    if entries:
        devices = []
        for entry in entries:
            devices.extend(
                device_reg.devices.get_devices_for_config_entry_id(entry.entry_id)
            )
        return devices
    return []


def get_device_id_from_name(hass: HomeAssistant, device_name: str) -> str:
    """Get the device id of the device with the given name."""

    def find_device_for_domain(domain: str, device_name: str) -> str | None:
        entries = list(
            hass.config_entries.async_entries(
                domain, include_ignore=False, include_disabled=False
            )
        )

        if entries:
            device_reg = dr.async_get(hass)
            for entry in entries:
                devices = device_reg.devices.get_devices_for_config_entry_id(
                    entry.entry_id
                )
                if devices:
                    for device in devices:
                        if device.name == device_name:
                            return device.id
        return None

    supported_device_domains = [BROWSERMOD_DOMAIN, REMOTE_ASSIST_DISPLAY_DOMAIN]

    for domain in supported_device_domains:
        if device_id := find_device_for_domain(domain, device_name):
            return device_id
    return None


def get_sensor_entity_from_instance(
    hass: HomeAssistant,
    entry_id: str,
) -> str:
    """Get VA sensor entity from config entry."""
    entity_registry = er.async_get(hass)
    if integration_entities := er.async_entries_for_config_entry(
        entity_registry, entry_id
    ):
        for entity in integration_entities:
            if entity.domain == Platform.SENSOR:
                return entity.entity_id
    return None


def get_entity_id_from_conversation_device_id(
    hass: HomeAssistant, device_id: str
) -> str | None:
    """Get the view assist entity id for a device id relating to the mic entity."""
    for entry in get_integration_entries(hass):
        mic_entity_id = entry.runtime_data.core.mic_device
        entity_registry = er.async_get(hass)
        mic_entity = entity_registry.async_get(mic_entity_id)
        if mic_entity and mic_entity.device_id == device_id:
            return get_sensor_entity_from_instance(hass, entry.entry_id)
    return None


def get_mimic_entity_id(hass: HomeAssistant, browser_id: str | None = None) -> str:
    """Get mimic entity id."""
    # If we reach here, no match for browser_id was found
    master_entry = get_master_config_entry(hass)
    if browser_id:
        if get_display_type_from_browser_id(hass, browser_id) == "native":
            if (
                master_entry.runtime_data.developer_settings.developer_device
                == browser_id
            ):
                return (
                    master_entry.runtime_data.developer_settings.developer_mimic_device
                )
            return None

        device_id = get_device_id_from_name(hass, browser_id)
        if master_entry.runtime_data.developer_settings.developer_device == device_id:
            return master_entry.runtime_data.developer_settings.developer_mimic_device
        return None
    return master_entry.runtime_data.developer_settings.developer_mimic_device


def get_entity_id_by_browser_id(hass: HomeAssistant, browser_id: str) -> str:
    """Get entity id form browser id.

    Support websocket
    """
    # Browser ID is same as device name, so get device id to VA device with display device
    # set to this id
    if browser_id.startswith("va-"):
        device_id = browser_id
    else:
        device_id = get_device_id_from_name(hass, browser_id)

    # Get all instances of view assist for browser id
    if device_id:
        entry_ids = [
            entry.entry_id
            for entry in get_integration_entries(hass)
            if entry.data.get(CONF_DISPLAY_DEVICE) == device_id
            and entry.disabled_by is None
        ]

        if entry_ids:
            return get_sensor_entity_from_instance(hass, entry_ids[0])

    return None


def get_mute_switch_entity_id(hass: HomeAssistant, mic_entity_id: str) -> str | None:
    """Get the mute switch entity id for a device id relating to the mic entity."""
    entity_registry = er.async_get(hass)
    if mic_entity := entity_registry.async_get(mic_entity_id):
        device_id = mic_entity.device_id
        device_entities = er.async_entries_for_device(entity_registry, device_id)
        for entity in device_entities:
            if entity.domain == "switch" and entity.entity_id.endswith(
                ("_mute", "_mic", "_microphone")
            ):
                return entity.entity_id
    return None


def get_hassmic_pipeline_status_entity_id(
    hass: HomeAssistant, mic_entity_id: str
) -> str | None:
    """Get the wakeword entity id for a hassmic device relating to the mic entity."""
    entity_registry = er.async_get(hass)
    if mic_entity := entity_registry.async_get(mic_entity_id):
        if mic_entity.platform != HASSMIC_DOMAIN:
            return None
        device_id = mic_entity.device_id
        device_entities = er.async_entries_for_device(entity_registry, device_id)
        for entity in device_entities:
            if entity.domain == "sensor" and entity.entity_id.endswith(
                "_pipeline_state"
            ):
                return entity.entity_id
    return None


def get_display_type_from_browser_id(
    hass: HomeAssistant, browser_id: str
) -> VADisplayType:
    """Return VAType from a browser id."""
    device_id = get_device_id_from_name(hass, browser_id)
    if device_id:
        device_reg = dr.async_get(hass)
        device = device_reg.async_get(device_id)

        entry = hass.config_entries.async_get_entry(device.primary_config_entry)
        if entry:
            if entry.domain == BROWSERMOD_DOMAIN:
                return VADisplayType.BROWSERMOD
            if entry.domain == REMOTE_ASSIST_DISPLAY_DOMAIN:
                return VADisplayType.REMOTE_ASSIST_DISPLAY
    return "native"


def get_revert_settings_for_mode(mode: VAMode) -> tuple:
    """Get revert settings from VAMODE_REVERTS for mode."""
    if mode in VAMODE_REVERTS:
        return VAMODE_REVERTS[mode].get("revert"), VAMODE_REVERTS[mode].get("view")
    return False, None


def get_assist_satellite_entity_id_from_device_id(
    hass: HomeAssistant, device_id: str
) -> str | None:
    """Get assist satellite entity id from device id."""
    device_entities = er.async_entries_for_device(er.async_get(hass), device_id)
    for entity in device_entities:
        if entity.domain == "assist_satellite":
            return entity.entity_id
    return None


def get_entities_by_attr_filter(
    hass: HomeAssistant,
    filter: dict[str, Any] | None = None,
    exclude: dict[str, Any] | None = None,
) -> list[str]:
    """Get the entity ids of devices not in dnd mode."""
    matched_entities = []
    entry_ids = [entry.entry_id for entry in get_integration_entries(hass)]
    for entry_id in entry_ids:
        entity_registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(entity_registry, entry_id)
        for entity in entities:
            if filter or exclude:
                if state := hass.states.get(entity.entity_id):
                    add_entity = False
                    if filter:
                        for attr, value in filter.items():
                            if state.attributes.get(attr) == value:
                                add_entity = True
                    if add_entity and exclude:
                        for attr, value in exclude.items():
                            if state.attributes.get(attr) == value:
                                add_entity = False
                    if add_entity:
                        matched_entities.append(entity.entity_id)
            else:
                matched_entities.append(entity.entity_id)
    return matched_entities


def get_key(
    dot_notation_path: str, data: dict
) -> dict[str, dict | str | int] | str | int:
    """Try to get a deep value from a dict based on a dot-notation."""

    try:
        if "." in dot_notation_path:
            dn_list = dot_notation_path.split(".")
        else:
            dn_list = [dot_notation_path]
        return reduce(dict.get, dn_list, data)
    except (TypeError, KeyError):
        return None


# ----------------------------------------------------------------
# Images
# ----------------------------------------------------------------
async def async_get_download_image(
    hass: HomeAssistant, config: VAConfigEntry, save_path: str = IMAGE_PATH
) -> Path:
    """Get url from unsplash random image endpoint."""
    return await hass.async_add_executor_job(
        get_download_image, hass, config, save_path
    )


def get_download_image(
    hass: HomeAssistant, config: VAConfigEntry, save_path: str = IMAGE_PATH
) -> Path:
    """Get url from unsplash random image endpoint."""
    path = Path(hass.config.config_dir, DOMAIN, save_path)
    filename = f"downloaded_{config.entry_id.lower()}_{slugify(config.runtime_data.core.name)}.jpg"
    image: Path | None = None

    try:
        response = requests.get(RANDOM_IMAGE_URL, timeout=10)
    except TimeoutError:
        _LOGGER.warning(
            "Timeout trying to fetch random image from %s", RANDOM_IMAGE_URL
        )
    else:
        if response.status_code == 200:
            try:
                # Ensure path exists
                path.mkdir(parents=True, exist_ok=True)
                with Path(path, filename).open(mode="wb") as file:
                    file.write(response.content)
            except OSError as ex:
                _LOGGER.warning(
                    "Unable to save downloaded random image file.  Error is %s", ex
                )

    image = Path(path, filename)
    if image.exists():
        return image

    _LOGGER.warning("No existing images found for background")
    return None


async def async_get_filesystem_images(hass: HomeAssistant, fs_path: str) -> list[Path]:
    """Get url from filesystem random image."""
    return await hass.async_add_executor_job(get_filesystem_images, hass, fs_path)


def get_filesystem_images(hass: HomeAssistant, fs_path: str) -> list[Path]:
    """Get url from filesystem random image."""
    valid_extensions = (".jpeg", ".jpg", ".tif", ".png")
    path = Path(hass.config.config_dir, DOMAIN, fs_path)
    if not path.exists():
        _LOGGER.warning("Random image path %s does not exist", path)
        return None

    image_list = [
        f for f in path.iterdir() if f.is_file and f.name.endswith(valid_extensions)
    ]

    # Check if any images were found
    if not image_list:
        _LOGGER.warning("No images found in random image path - %s", path)
        return None

    return image_list


def differ_to_json(diffs: list) -> dict:
    """Convert dictdiffer output to json for saving to file."""
    output = {}
    for diff in diffs:
        chg_type = diff[0]
        if not output.get(chg_type):
            output[chg_type] = []

        if chg_type in ("add", "remove"):
            output[chg_type].append(
                {
                    "path": diff[1],
                    "key": diff[2][0][0],
                    "value": diff[2][0][1],
                }
            )
        elif chg_type == "change":
            output[chg_type].append(
                {
                    "path": diff[1],
                    "orig": diff[2][0],
                    "updated": diff[2][1],
                }
            )

    return output


def json_to_dictdiffer(jsondiff: dict) -> list:
    """Convert json to dictdiffer format for rebuiling changes."""
    output = []
    for chg_type, changes in jsondiff.items():
        for change in changes:
            if chg_type in ("add", "remove"):
                output.append(
                    (chg_type, change["path"], [(change["key"], change["value"])])
                )
            elif chg_type == "change":
                output.append(
                    (chg_type, change["path"], (change["orig"], change["updated"]))
                )

    return output


def get_available_overlays(hass: HomeAssistant) -> dict[str, str]:
    """Get available overlays for pipeline listening, processing, etc."""
    # Read the HTML file
    overlays = {}
    path = hass.config.path(DOMAIN, DASHBOARD_DIR, f"{OVERLAY_FILE_NAME}.html")
    if Path(path).exists():
        content = Path(path).read_text(encoding="utf-8")

        # Parse the HTML content
        soup = BeautifulSoup(content, "html.parser")

        # Print the href attribute of each link
        for div in soup.find_all("div", recursive=False):
            o_id = div.get("id")
            name = div.get("data-name")
            if o_id and name:
                overlays[o_id] = name
    if overlays:
        return overlays
    return {}
