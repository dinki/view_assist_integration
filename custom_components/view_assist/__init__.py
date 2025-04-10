"""View Assist custom integration."""

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.start import async_at_started

from .alarm_repeater import ALARMS, VAAlarmRepeater
from .const import DOMAIN, OPTION_KEY_MIGRATIONS, RuntimeData, VAConfigEntry
from .dashboard import DASHBOARD_MANAGER, DashboardManager
from .entity_listeners import EntityListeners
from .helpers import (
    ensure_list,
    get_device_name_from_id,
    get_loaded_instance_count,
    is_first_instance,
)
from .http_url import HTTPManager
from .js_modules import JSModuleRegistration
from .master_config import MASTER_CONFIG, MasterConfigManager
from .services import VAServices
from .templates import setup_va_templates
from .timers import TIMERS, VATimers
from .websocket import async_register_websockets

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


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
    if entry.minor_version == 1 and entry.options:
        new_options = {**entry.options}
        # Migrate options keys
        for key, value in new_options.items():
            if isinstance(value, str) and value in OPTION_KEY_MIGRATIONS:
                new_options[key] = OPTION_KEY_MIGRATIONS.get(value)

        hass.config_entries.async_update_entry(
            entry, options=new_options, minor_version=2, version=1
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

    # Add runtime data to config entry to have place to store data and
    # make accessible throughout integration
    entry.runtime_data = RuntimeData()
    set_runtime_data_from_config(entry)
    
    # Handle intent_device specially - if not in config, don't set it
    if "intent_device" not in entry.data:
        entry.runtime_data.intent_device = ""

    # Add config change listener
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Run first instance only functions
    if is_first_instance(hass, entry, display_instance_only=False):
        await run_if_first_instance(hass, entry)

    # Run first display instance only functions
    if is_first_instance(hass, entry, display_instance_only=True):
        await run_if_first_display_instance(hass, entry)

    # Load entity listeners
    EntityListeners(hass, entry)

    # Request platform setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Fire display device registration to setup display if first time config
    async_dispatcher_send(
        hass,
        f"{DOMAIN}_{get_device_name_from_id(hass, entry.runtime_data.display_device)}_registered",
    )

    return True

async def run_if_first_instance(hass: HomeAssistant, entry: VAConfigEntry):
    """Things to run only for first instance of integration."""
    master_config = MasterConfigManager(hass, entry)
    hass.data[DOMAIN][MASTER_CONFIG] = master_config
    await master_config.load()

    # Inisitialise service
    services = VAServices(hass, entry)
    await services.async_setup_services()

    # Setup Timers
    timers = VATimers(hass, entry)
    hass.data[DOMAIN][TIMERS] = timers
    await timers.load()

    # Load javascript modules
    jsloader = JSModuleRegistration(hass)
    await jsloader.async_register()

    hass.data[DOMAIN][ALARMS] = VAAlarmRepeater(hass, entry)

    setup_va_templates(hass)


async def run_if_first_display_instance(hass: HomeAssistant, entry: VAConfigEntry):
    """Things to run only one when multiple instances exist."""

    # Run dashboard and view setup
    async def setup_frontend(*args):
        # Load websockets
        await async_register_websockets(hass)

        http = HTTPManager(hass, entry)
        await http.create_url_paths()

        dm = DashboardManager(hass, entry)
        hass.data[DOMAIN][DASHBOARD_MANAGER] = dm
        await dm.setup_dashboard()

    async_at_started(hass, setup_frontend)


def set_runtime_data_from_config(config_entry: VAConfigEntry):
    """Set config.runtime_data attributes from matching config values."""

    config_sources = [config_entry.data, config_entry.options]
    for source in config_sources:
        for k, v in source.items():
            if hasattr(config_entry.runtime_data, k):
                # This is a fix for config lists being a string
                if isinstance(getattr(config_entry.runtime_data, k), list):
                    setattr(config_entry.runtime_data, k, ensure_list(v))
                else:
                    setattr(config_entry.runtime_data, k, v)


async def _async_update_listener(hass: HomeAssistant, config_entry: VAConfigEntry):
    """Handle config options update."""
    # Reload the integration when the options change.
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: VAConfigEntry):
    """Unload a config entry."""

    # Unload js resources
    if get_loaded_instance_count(hass) <= 1:
        # Unload lovelace module resource if only instance
        _LOGGER.debug("Removing javascript modules cards")
        jsloader = JSModuleRegistration(hass)
        await jsloader.async_unregister()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
