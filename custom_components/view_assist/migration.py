"""Migration code for View Assist config entries."""

import logging

from homeassistant.core import HomeAssistant

from .const import (
    CONF_ASSIST_PROMPT,
    CONF_BACKGROUND,
    CONF_BACKGROUND_MODE,
    CONF_BACKGROUND_SETTINGS,
    CONF_DEV_MIMIC,
    CONF_DISPLAY_SETTINGS,
    CONF_DO_NOT_DISTURB,
    CONF_FONT_STYLE,
    CONF_HIDE_HEADER,
    CONF_HIDE_SIDEBAR,
    CONF_MIC_TYPE,
    CONF_MIC_UNMUTE,
    CONF_ROTATE_BACKGROUND,
    CONF_ROTATE_BACKGROUND_INTERVAL,
    CONF_ROTATE_BACKGROUND_LINKED_ENTITY,
    CONF_ROTATE_BACKGROUND_PATH,
    CONF_ROTATE_BACKGROUND_SOURCE,
    CONF_SCREEN_MODE,
    CONF_STATUS_ICON_SIZE,
    CONF_STATUS_ICONS,
    CONF_TIME_FORMAT,
    CONF_USE_24H_TIME,
    CONF_USE_ANNOUNCE,
    OPTION_KEY_MIGRATIONS,
)
from .typed import VABackgroundMode, VAConfigEntry, VAScreenMode, VATimeFormat

_LOGGER = logging.getLogger(__name__)


async def async_migrate_view_assist_config_entry(
    hass: HomeAssistant,
    entry: VAConfigEntry,
) -> bool:
    """Migrate config entry if needed."""
    # No migration needed
    _LOGGER.debug(
        "Config Migration from v%s.%s - %s",
        entry.version,
        entry.minor_version,
        entry.options,
    )
    new_options = {**entry.options}
    if entry.minor_version < 2 and entry.options:
        # Migrate options keys
        for key, value in new_options.items():
            if isinstance(value, str) and value in OPTION_KEY_MIGRATIONS:
                new_options[key] = OPTION_KEY_MIGRATIONS.get(value)

    if entry.minor_version < 3 and entry.options:
        # Remove mic_type key
        if "mic_type" in entry.options:
            new_options.pop(CONF_MIC_TYPE)

    if entry.minor_version < 4:
        # Migrate to master config model

        # Remove mimic device key as moved into master config
        new_options.pop(CONF_DEV_MIMIC, None)

        # Dashboard options
        # Background has both moved into a section and also changed parameters
        # Add section and migrate values
        if CONF_BACKGROUND_SETTINGS not in new_options:
            new_options[CONF_BACKGROUND_SETTINGS] = {}

        for param in (
            CONF_ROTATE_BACKGROUND,
            CONF_BACKGROUND,
            CONF_ROTATE_BACKGROUND_PATH,
            CONF_ROTATE_BACKGROUND_INTERVAL,
            CONF_ROTATE_BACKGROUND_LINKED_ENTITY,
        ):
            if param in new_options:
                if param == CONF_ROTATE_BACKGROUND:
                    new_options[CONF_BACKGROUND_SETTINGS][CONF_BACKGROUND_MODE] = (
                        VABackgroundMode.DEFAULT_BACKGROUND
                        if new_options[param] is False
                        else new_options[CONF_ROTATE_BACKGROUND_SOURCE]
                    )
                    new_options.pop(param, None)
                    new_options.pop(CONF_ROTATE_BACKGROUND_SOURCE, None)
                else:
                    new_options[CONF_BACKGROUND_SETTINGS][param] = new_options.pop(
                        param, None
                    )

        # Display options
        # Display options has both moved into a section and also changed parameters
        if CONF_DISPLAY_SETTINGS not in new_options:
            new_options[CONF_DISPLAY_SETTINGS] = {}

        for param in [
            CONF_ASSIST_PROMPT,
            CONF_STATUS_ICON_SIZE,
            CONF_FONT_STYLE,
            CONF_STATUS_ICONS,
            CONF_USE_24H_TIME,
            CONF_HIDE_HEADER,
        ]:
            if param in new_options:
                if param == CONF_USE_24H_TIME:
                    new_options[CONF_DISPLAY_SETTINGS][CONF_TIME_FORMAT] = (
                        VATimeFormat.HOUR_24
                        if entry.options[param]
                        else VATimeFormat.HOUR_12
                    )
                    new_options.pop(param)
                elif param == CONF_HIDE_HEADER:
                    mode = 0
                    if new_options.pop(CONF_HIDE_HEADER, None):
                        mode += 1
                    if new_options.pop(CONF_HIDE_SIDEBAR, None):
                        mode += 2
                    new_options[CONF_DISPLAY_SETTINGS][CONF_SCREEN_MODE] = list(
                        VAScreenMode
                    )[mode].value
                else:
                    new_options[CONF_DISPLAY_SETTINGS][param] = new_options.pop(param)

    if entry.minor_version < 5:
        # Fix for none migration of default options for dnd, announce and unmute mic
        for key in [CONF_DO_NOT_DISTURB, CONF_USE_ANNOUNCE, CONF_MIC_UNMUTE]:
            if new_options.get(key) is not None:
                new_options[CONF_DO_NOT_DISTURB] = (
                    "on" if new_options.get(key) else "off"
                )

    if new_options != entry.options:
        hass.config_entries.async_update_entry(
            entry, options=new_options, minor_version=5, version=1
        )

        _LOGGER.debug(
            "Migration to configuration version %s.%s successful",
            entry.version,
            entry.minor_version,
        )
    return True
