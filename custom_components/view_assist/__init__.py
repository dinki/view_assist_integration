"""View Assist custom integration."""

import logging

from homeassistant import config_entries
from homeassistant.const import CONF_TYPE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.start import async_at_started

from .alarm_repeater import ALARMS, VAAlarmRepeater
from .assets import ASSETS_MANAGER, AssetsManager
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
    DEFAULT_VALUES,
    DOMAIN,
    OPTION_KEY_MIGRATIONS,
)
from .entity_listeners import EntityListeners
from .helpers import (
    ensure_list,
    get_device_name_from_id,
    get_integration_entries,
    get_key,
    get_master_config_entry,
    is_first_instance,
)
from .http_url import HTTPManager
from .js_modules import JSModuleRegistration
from .menu_manager import MenuManager
from .services import VAServices
from .templates import setup_va_templates
from .timers import TIMERS, VATimers
from .typed import (
    DeviceCoreConfig,
    DeviceRuntimeData,
    MasterConfigRuntimeData,
    VABackgroundMode,
    VAConfigEntry,
    VAEvent,
    VAScreenMode,
    VATimeFormat,
    VAType,
)
from .websocket import async_register_websockets

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


def migrate_to_section(entry: VAConfigEntry, params: list[str]):
    """Build a section for the config entry."""
    section = {}
    for param in params:
        if entry.options.get(param):
            section[param] = entry.options.pop(param)
    return section


async def async_migrate_entry(
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


async def async_setup_entry(hass: HomeAssistant, entry: VAConfigEntry):
    """Set up View Assist from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    has_master_entry = get_master_config_entry(hass)
    is_master_entry = has_master_entry and entry.data[CONF_TYPE] == VAType.MASTER_CONFIG

    # Add runtime data to config entry to have place to store data and
    # make accessible throughout integration
    set_runtime_data_for_config(hass, entry, is_master_entry)

    # Add config change listener
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not has_master_entry:
        # Start a config flow to add a master entry if no master entry
        _LOGGER.debug("No master entry found, starting config flow")
        if is_first_instance(hass, entry):
            discovery_flow.async_create_flow(
                hass,
                DOMAIN,
                {"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                {"name": VAType.MASTER_CONFIG},
            )

        # Run first instance only functions
        if is_first_instance(hass, entry, display_instance_only=False):
            await load_common_functions(hass, entry)

        # Run first display instance only functions
        if is_first_instance(hass, entry, display_instance_only=True):
            await load_common_display_functions(hass, entry)

    if is_master_entry:
        await load_common_functions(hass, entry)
        await load_common_display_functions(hass, entry)

    else:
        # Set entity listeners
        EntityListeners(hass, entry)

        # Request platform setup
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Fire display device registration to setup display when added or re-enabled
        if entry.data[CONF_TYPE] == VAType.VIEW_AUDIO:
            async_dispatcher_send(
                hass,
                f"{DOMAIN}_{get_device_name_from_id(hass, entry.runtime_data.core.display_device)}_registered",
            )

    return True


async def load_common_functions(hass: HomeAssistant, entry: VAConfigEntry):
    """Things to run only for first instance of integration."""

    # Inisitialise service
    services = VAServices(hass, entry)
    await services.async_setup_services()

    # Setup Timers
    timers = VATimers(hass, entry)
    hass.data[DOMAIN][TIMERS] = timers
    await timers.load()

    # Setup Menu Manager
    menu_manager = MenuManager(hass, entry)
    hass.data[DOMAIN]["menu_manager"] = menu_manager

    # Load javascript modules
    jsloader = JSModuleRegistration(hass)
    await jsloader.async_register()

    hass.data[DOMAIN][ALARMS] = VAAlarmRepeater(hass, entry)

    setup_va_templates(hass)

    # Load asset manager
    am = AssetsManager(hass, entry)
    hass.data[DOMAIN][ASSETS_MANAGER] = am
    await am.async_setup()

    # Request update platform setup
    if entry.runtime_data.integration.enable_updates:
        _LOGGER.debug("Update notifications are enabled")
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.UPDATE])


async def load_common_display_functions(hass: HomeAssistant, entry: VAConfigEntry):
    """Things to run only one when multiple instances exist."""

    # Run dashboard and view setup
    async def setup_frontend(*args):
        # Initiate var to hold VA browser ids.  Do not reset if exists
        # as this is used to track browser ids across reloads
        if not hass.data[DOMAIN].get("va_browser_ids"):
            hass.data[DOMAIN]["va_browser_ids"] = {}
        # Load websockets
        await async_register_websockets(hass)

        http = HTTPManager(hass, entry)
        await http.create_url_paths()

    async_at_started(hass, setup_frontend)


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
                        if sub_value := get_config_value(
                            f"{attr}.{sub_attr}", is_master=is_master
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

    # Default options - doesn't yet handle sections
    for attr in r.default.__dict__:
        if value := get_config_value(attr, is_master=is_master):
            setattr(r.default, attr, value)


async def _async_update_listener(hass: HomeAssistant, config_entry: VAConfigEntry):
    """Handle config options update."""
    # Reload the integration when the options change.
    is_master = config_entry.data[CONF_TYPE] == VAType.MASTER_CONFIG
    if is_master:
        if entries := get_integration_entries(hass):
            for entry in entries:
                await hass.config_entries.async_reload(entry.entry_id)
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VAConfigEntry):
    """Unload a config entry."""

    # Unload js resources
    if entry.data[CONF_TYPE] == VAType.MASTER_CONFIG:
        # Unload lovelace module resource if only instance
        _LOGGER.debug("Removing javascript modules cards")
        jsloader = JSModuleRegistration(hass)
        await jsloader.async_unregister()
        await hass.config_entries.async_unload_platforms(entry, [Platform.UPDATE])
        return True

    # Tell device to unregister
    _LOGGER.debug("Unregistering device: %s", entry.entry_id)
    async_dispatcher_send(
        hass,
        f"{DOMAIN}_{entry.entry_id}_event",
        VAEvent("unregistered"),
    )
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
