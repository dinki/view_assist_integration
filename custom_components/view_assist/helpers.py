"""Helper functions."""

from functools import reduce
import logging
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from homeassistant.const import CONF_TYPE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from homeassistant.util.yaml import load_yaml_dict

from .const import (
    BROWSERMOD_DOMAIN,
    COMMUNITY_BLUEPRINTS_DIR,
    COMMUNITY_VIEWS_DIR,
    CONF_DISPLAY_DEVICE,
    CORE_VIEWS,
    CUSTOM_BLUEPRINTS_DIR,
    CUSTOM_VIEWS_DIR,
    DASHBOARD_DIR,
    DOMAIN,
    HASSMIC_DOMAIN,
    OVERLAY_FILE_NAME,
    REMOTE_ASSIST_DISPLAY_DOMAIN,
    VAMODE_REVERTS,
    VAMode,
    VIEWS_DIR,
)
from .typed import VAConfigEntry, VADisplayType, VAType, DISPLAY_DEVICE_TYPES

_LOGGER = logging.getLogger(__name__)


def get_integration_entries(
    hass: HomeAssistant,
    accepted_types: list[VAType] | None = None,
) -> list[VAConfigEntry]:
    """Get list of config entries for the integration."""
    if accepted_types is None:
        accepted_types = [*DISPLAY_DEVICE_TYPES, VAType.AUDIO_ONLY]
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
    accepted_types = DISPLAY_DEVICE_TYPES
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


def get_mic_device_domain(hass: HomeAssistant, entity_id: str) -> str | None:
    """Get the mic device domain of an entity by id."""
    entity_registry = er.async_get(hass)
    if va_entity := entity_registry.async_get(entity_id):
        va_entry = hass.config_entries.async_get_entry(va_entity.config_entry_id)
        if mic_entity := va_entry.data.get("mic_device"):
            mic_entity_entry = entity_registry.async_get(mic_entity)
            if mic_entity_entry:
                entry_id = mic_entity_entry.config_entry_id
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry:
                    return entry.domain
    return None


def get_mic_device_id_from_entity_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """Get the mic device id of an entity by id."""
    entity_registry = er.async_get(hass)
    if va_entity := entity_registry.async_get(entity_id):
        va_entry = hass.config_entries.async_get_entry(va_entity.config_entry_id)
        if mic_entity := va_entry.data.get("mic_device"):
            return entity_registry.async_get(mic_entity).device_id
    return None


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
    paths = [hass.config.path(DOMAIN, DASHBOARD_DIR, f"{OVERLAY_FILE_NAME}.html")]
    custom_path = hass.config.path(
        DOMAIN, "custom_overlays", f"{OVERLAY_FILE_NAME}.html"
    )
    if Path(custom_path).exists():
        paths.append(custom_path)

    for path in paths:
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


def get_available_core_views(hass: HomeAssistant) -> dict[str, Any]:
    """Get catalog of available core views and variants."""
    return CORE_VIEWS


def get_available_core_view_variants(
    hass: HomeAssistant,
) -> dict[str, dict[str, str]]:
    """Scan local core view directories and return available variants for each core view."""
    results = {}
    base_views_dir = Path(hass.config.path(DOMAIN, VIEWS_DIR))

    for core_name, core_info in CORE_VIEWS.items():
        variants_dict: dict[str, str] = {}
        core_folder = base_views_dir / core_name

        # 1. Registered variants in CORE_VIEWS definition
        for var_key, var_data in core_info.get("variants", {}).items():
            var_file = var_data.get("file", "")
            if "/" in var_file:
                target_file = base_views_dir / var_file
            else:
                target_file = core_folder / var_file

            if target_file.exists() or var_key == "standard":
                variants_dict[var_key] = var_data.get(
                    "name", var_key.replace("_", " ").title()
                )

        # 2. Dynamic discovery: any other .yaml files in the core view's directory
        if core_folder.exists():
            for item in core_folder.iterdir():
                if item.is_file() and item.suffix in (".yaml", ".yml"):
                    matching_registered = [
                        k
                        for k, v in core_info.get("variants", {}).items()
                        if v.get("file") == item.name
                    ]
                    if (
                        not matching_registered
                        and item.name != f"{core_name}.saved.yaml"
                        and not item.name.endswith(".saved.yaml")
                    ):
                        try:
                            data = load_yaml_dict(str(item)) or {}
                        except Exception:  # noqa: BLE001
                            data = {}
                        title = data.get("title") if isinstance(data, dict) else None
                        if not title:
                            title = (
                                item.stem.replace("_", " ").replace("-", " ").title()
                            )
                        var_key = item.stem
                        variants_dict[var_key] = f"{title} ({item.name})"

        # Only add to results if there is more than 1 option (meaning there is an actual choice to make)
        if len(variants_dict) > 1:
            results[core_name] = variants_dict

    return results


def get_available_community_views(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Get available community contribution views from local cache."""
    views = {}
    community_dir = Path(hass.config.path(DOMAIN, VIEWS_DIR, COMMUNITY_VIEWS_DIR))
    if not community_dir.exists():
        return views

    for item in community_dir.iterdir():
        if item.is_file() and item.suffix in (".yaml", ".yml"):
            key = item.stem
            try:
                data = load_yaml_dict(str(item)) or {}
            except Exception:  # noqa: BLE001
                data = {}
            title = data.get("title") if isinstance(data, dict) else None
            if not title:
                title = key.replace("_", " ").replace("-", " ").title()

            views[key] = {
                "key": key,
                "title": title,
                "path": data.get("path", key) if isinstance(data, dict) else key,
                "file": item.name,
                "full_path": str(item),
                "source": "community",
                "linked_blueprints": find_linked_blueprints_for_view(
                    hass, key, "community"
                ),
            }
        elif item.is_dir():
            yaml_file = item / f"{item.name}.yaml"
            if yaml_file.exists():
                key = item.name
                try:
                    data = load_yaml_dict(str(yaml_file)) or {}
                except Exception:  # noqa: BLE001
                    data = {}
                title = data.get("title") if isinstance(data, dict) else None
                if not title:
                    title = key.replace("_", " ").replace("-", " ").title()
                views[key] = {
                    "key": key,
                    "title": title,
                    "path": data.get("path", key) if isinstance(data, dict) else key,
                    "file": f"{item.name}/{yaml_file.name}",
                    "full_path": str(yaml_file),
                    "source": "community",
                    "linked_blueprints": find_linked_blueprints_for_view(
                        hass, key, "community"
                    ),
                }

    return views


def get_available_custom_views(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Get available user custom views from local custom folder."""
    views = {}
    custom_dir = Path(hass.config.path(DOMAIN, VIEWS_DIR, CUSTOM_VIEWS_DIR))
    if not custom_dir.exists():
        return views

    for item in custom_dir.iterdir():
        if item.is_file() and item.suffix in (".yaml", ".yml"):
            key = item.stem
            try:
                data = load_yaml_dict(str(item)) or {}
            except Exception:  # noqa: BLE001
                data = {}
            title = data.get("title") if isinstance(data, dict) else None
            if not title:
                title = key.replace("_", " ").replace("-", " ").title()

            views[key] = {
                "key": key,
                "title": f"{title} [Custom]",
                "path": data.get("path", key) if isinstance(data, dict) else key,
                "file": item.name,
                "full_path": str(item),
                "source": "custom",
                "linked_blueprints": find_linked_blueprints_for_view(
                    hass, key, "custom"
                ),
            }
        elif item.is_dir():
            yaml_file = item / f"{item.name}.yaml"
            if yaml_file.exists():
                key = item.name
                try:
                    data = load_yaml_dict(str(yaml_file)) or {}
                except Exception:  # noqa: BLE001
                    data = {}
                title = data.get("title") if isinstance(data, dict) else None
                if not title:
                    title = key.replace("_", " ").replace("-", " ").title()
                views[key] = {
                    "key": key,
                    "title": f"{title} [Custom]",
                    "path": data.get("path", key) if isinstance(data, dict) else key,
                    "file": f"{item.name}/{yaml_file.name}",
                    "full_path": str(yaml_file),
                    "source": "custom",
                    "linked_blueprints": find_linked_blueprints_for_view(
                        hass, key, "custom"
                    ),
                }

    return views


def find_linked_blueprints_for_view(
    hass: HomeAssistant, view_key: str, source: str = "community"
) -> list[str]:
    """Find blueprint filenames associated with a given community or custom view."""
    linked = []
    sub_dir = (
        COMMUNITY_BLUEPRINTS_DIR if source == "community" else CUSTOM_BLUEPRINTS_DIR
    )
    bp_base = Path(hass.config.path(DOMAIN, "blueprints", sub_dir))
    if not bp_base.exists():
        return linked

    clean_key = view_key.lower().replace("_", "").replace("-", "")

    # Check for direct matching subdirectory (e.g. Slideshow/)
    for item in bp_base.iterdir():
        clean_item = item.name.lower().replace("_", "").replace("-", "")
        if item.is_dir() and clean_item == clean_key:
            for bp_file in item.glob("*.yaml"):
                linked.append(f"{item.name}/{bp_file.name}")
        elif item.is_file() and item.suffix in (".yaml", ".yml"):
            if clean_key in clean_item:
                linked.append(item.name)

    return linked


def get_available_community_blueprints(
    hass: HomeAssistant,
) -> dict[str, dict[str, Any]]:
    """Get standalone community blueprints from local cache."""
    blueprints = {}
    bp_dir = Path(hass.config.path(DOMAIN, "blueprints", COMMUNITY_BLUEPRINTS_DIR))
    if not bp_dir.exists():
        return blueprints

    for item in bp_dir.rglob("*.yaml"):
        # Ignore non-blueprint folders
        if any(part in ("custom_sentences", "intent_script") for part in item.parts):
            continue

        try:
            data = load_yaml_dict(str(item)) or {}
        except Exception:  # noqa: BLE001
            data = {}

        is_bp = item.name.startswith("blueprint-") or (
            isinstance(data, dict) and "blueprint" in data
        )
        if not is_bp:
            continue

        rel = item.relative_to(bp_dir)
        key = str(rel).replace("\\", "/")

        bp_meta = data.get("blueprint", {}) if isinstance(data, dict) else {}
        name = (
            bp_meta.get("name")
            if isinstance(bp_meta, dict)
            else (data.get("name") if isinstance(data, dict) else None)
        )
        if not name:
            name = item.stem.replace("blueprint-", "").replace("_", " ").title()
        desc = (
            bp_meta.get("description", "")
            if isinstance(bp_meta, dict)
            else (data.get("description", "") if isinstance(data, dict) else "")
        )

        blueprints[key] = {
            "key": key,
            "name": name,
            "description": desc,
            "file": item.name,
            "full_path": str(item),
            "folder": str(rel.parent) if rel.parent != Path(".") else None,
            "source": "community",
        }

    return blueprints


def get_available_custom_blueprints(
    hass: HomeAssistant,
) -> dict[str, dict[str, Any]]:
    """Get custom blueprints from local custom folder."""
    blueprints = {}
    bp_dir = Path(hass.config.path(DOMAIN, "blueprints", CUSTOM_BLUEPRINTS_DIR))
    if not bp_dir.exists():
        return blueprints

    for item in bp_dir.rglob("*.yaml"):
        # Ignore non-blueprint folders
        if any(part in ("custom_sentences", "intent_script") for part in item.parts):
            continue

        try:
            data = load_yaml_dict(str(item)) or {}
        except Exception:  # noqa: BLE001
            data = {}

        is_bp = item.name.startswith("blueprint-") or (
            isinstance(data, dict) and "blueprint" in data
        )
        if not is_bp:
            continue

        rel = item.relative_to(bp_dir)
        key = str(rel).replace("\\", "/")

        bp_meta = data.get("blueprint", {}) if isinstance(data, dict) else {}
        name = (
            bp_meta.get("name")
            if isinstance(bp_meta, dict)
            else (data.get("name") if isinstance(data, dict) else None)
        )
        if not name:
            name = item.stem.replace("blueprint-", "").replace("_", " ").title()
        desc = (
            bp_meta.get("description", "")
            if isinstance(bp_meta, dict)
            else (data.get("description", "") if isinstance(data, dict) else "")
        )

        blueprints[key] = {
            "key": key,
            "name": f"{name} [Custom]",
            "description": desc,
            "file": item.name,
            "full_path": str(item),
            "folder": str(rel.parent) if rel.parent != Path(".") else None,
            "source": "custom",
        }

    return blueprints
