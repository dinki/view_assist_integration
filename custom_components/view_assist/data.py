"""Runtime data management for View Assist."""

import asyncio
import logging

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BACKGROUND_SETTINGS,
    CONF_DISPLAY_SETTINGS,
    DEFAULT_VALUES,
    DOMAIN,
)
from .helpers import ensure_list, get_key, get_master_config_entry
from .typed import (
    DeviceCoreConfig,
    DeviceRuntimeData,
    MasterConfigRuntimeData,
    VAConfigEntry,
)

TIMEOUT = 120
_LOGGER = logging.getLogger(__name__)


def set_runtime_data_for_config(  # noqa: C901
    hass: HomeAssistant, config_entry: VAConfigEntry, is_master: bool = False
):
    """Set config.runtime_data attributes from matching config values."""

    def get_config_value(
        attr: str, is_master: bool = False
    ) -> str | float | list | None:
        value = get_key(attr, dict(config_entry.options))
        if not is_master and (
            value is None
            or (isinstance(value, dict) and not value)
            or (isinstance(value, list) and not value)
        ):
            value = get_key(attr, dict(master_config_options))
        if value is None or (isinstance(value, dict) and not value):
            value = get_key(attr, DEFAULT_VALUES)

        # This is a fix for config lists being a string
        if isinstance(attr, list):
            value = ensure_list(value)
        return value

    master_config_options = (
        get_master_config_entry(hass).options if get_master_config_entry(hass) else {}
    )

    if is_master:
        r = config_entry.runtime_data = MasterConfigRuntimeData()
        # Dashboard options - handles sections
        for attr in r.dashboard.__dict__:
            if value := get_config_value(attr, is_master=True):
                try:
                    if attr in (CONF_BACKGROUND_SETTINGS, CONF_DISPLAY_SETTINGS):
                        values = {}
                        for sub_attr in getattr(r.dashboard, attr).__dict__:
                            if sub_value := get_config_value(
                                f"{attr}.{sub_attr}", is_master=True
                            ):
                                values[sub_attr] = sub_value
                        value = type(getattr(r.dashboard, attr))(**values)
                    setattr(r.dashboard, attr, value)
                except Exception as ex:  # noqa: BLE001
                    _LOGGER.error(
                        "Error setting runtime data for %s - %s: %s",
                        attr,
                        type(getattr(r.dashboard, attr)),
                        str(ex),
                    )

        # Integration options
        for attr in r.integration.__dict__:
            value = get_config_value(attr, is_master=True)
            if value is not None:
                setattr(r.integration, attr, value)

        # Developer options
        for attr in r.developer_settings.__dict__:
            if value := get_config_value(attr, is_master=True):
                setattr(r.developer_settings, attr, value)
    else:
        r = config_entry.runtime_data = DeviceRuntimeData()
        r.core = DeviceCoreConfig(**config_entry.data)
        master_config_options = (
            get_master_config_entry(hass).options
            if get_master_config_entry(hass)
            else {}
        )
        # Dashboard options - handles sections
        for attr in r.dashboard.__dict__:
            if value := get_config_value(attr):
                try:
                    if isinstance(value, dict):
                        values = {}
                        for sub_attr in getattr(r.dashboard, attr).__dict__:
                            if sub_value := get_config_value(f"{attr}.{sub_attr}"):
                                values[sub_attr] = sub_value
                        value = type(getattr(r.dashboard, attr))(**values)
                    setattr(r.dashboard, attr, value)
                except Exception as ex:  # noqa: BLE001
                    _LOGGER.error(
                        "Error setting runtime data for %s - %s: %s",
                        attr,
                        type(getattr(r.dashboard, attr)),
                        str(ex),
                    )

    # Dashboard options - handles sections - master and non master
    for attr in r.dashboard.__dict__:
        if value := get_config_value(attr, is_master=is_master):
            try:
                if attr in (CONF_BACKGROUND_SETTINGS, CONF_DISPLAY_SETTINGS):
                    values = {}
                    for sub_attr in getattr(r.dashboard, attr).__dict__:
                        if (
                            sub_value := get_config_value(
                                f"{attr}.{sub_attr}", is_master=is_master
                            )
                        ) is not None:
                            values[sub_attr] = sub_value
                    value = type(getattr(r.dashboard, attr))(**values)
                setattr(r.dashboard, attr, value)
            except Exception as ex:  # noqa: BLE001
                _LOGGER.error(
                    "Error setting runtime data for %s - %s: %s",
                    attr,
                    type(getattr(r.dashboard, attr)),
                    str(ex),
                )

    # Default options - doesn't yet handle sections
    for attr in r.default.__dict__:
        if value := get_config_value(attr, is_master=is_master):
            setattr(r.default, attr, value)


async def wait_for_master_config(hass: HomeAssistant, entry: VAConfigEntry) -> bool:
    """Wait for the master config to be fully loaded."""
    try:
        async with asyncio.timeout(60):
            while True:
                try:
                    if hass.data[DOMAIN]["master_config_loaded"]:
                        break
                except KeyError:
                    pass
                _LOGGER.debug(
                    "Waiting for master config to be fully loaded before continuing setup for %s",
                    entry.entry_id,
                )
                await asyncio.sleep(0.5)
    except TimeoutError:
        _LOGGER.error(
            "Timeout waiting for master config to load before continuing setup for %s",
            entry.entry_id,
        )
        return False
    else:
        return True
