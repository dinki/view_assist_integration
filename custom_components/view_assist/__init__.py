import asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, CONF_PATH, Platform
from homeassistant.core import (
    HassJob,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
import voluptuous as vol
from homeassistant.helpers.event import async_call_later, partial
from homeassistant.util import timedelta
from .const import DOMAIN
from homeassistant.helpers.selector import selector

# import homeassistant.helpers.entity_registry as er
from homeassistant.helpers import entity_registry as er

import logging

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

NAVIGATE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE): selector(
            {"entity": {"filter": {"integration": DOMAIN}}}
        ),
        vol.Required(CONF_PATH): str,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up View Assist from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    # Request platform setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    ##################
    # Get Target Satellite
    # Used to determine which VA satellite is being used based on its microphone device
    #
    # Sample usage
    # action: view_assist.get_target_satellite
    # data:
    #   device_id: 4385828338e48103f63c9f91756321df

    async def handle_get_target_satellite(call: ServiceCall) -> ServiceResponse:
        """Handle a get target satellite lookup call."""
        device_id = call.data.get("device_id")
        entity_registry = er.async_get(hass)

        entities = []

        entry_ids = [
            entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)
        ]

        for entry_id in entry_ids:
            integration_entities = er.async_entries_for_config_entry(
                entity_registry, entry_id
            )
            entity_ids = [entity.entity_id for entity in integration_entities]
            entities.extend(entity_ids)

        # Fetch the 'mic_device' attribute for each entity
        # compare the device_id of mic_device to the value passed in to the service
        # return the match for the satellite that contains that mic_device
        target_satellite_devices = []
        for entity_id in entities:
            if state := hass.states.get(entity_id):
                if mic_entity_id := state.attributes.get("mic_device"):
                    if mic_entity := entity_registry.async_get(mic_entity_id):
                        if mic_entity.device_id == device_id:
                            target_satellite_devices.append(entity_id)

        # Return the list of target_satellite_devices
        # This should match only one VA device
        return {"target_satellite": target_satellite_devices}

    hass.services.async_register(
        DOMAIN,
        "get_target_satellite",
        handle_get_target_satellite,
        supports_response=SupportsResponse.ONLY,
    )

    #########

    #########
    # Handle Navigation
    # Used to determine how to change the view on the VA device
    #
    # action: view_assist.navigate
    # data:
    #   target_display_device: sensor.viewassist_office_browser_path
    #   target_display_type: browsermod
    #   path: /dashboard-viewassist/weather
    #
    async def handle_navigate(call: ServiceCall):
        """Handle a navigate to view call."""
        va_entity_id = call.data.get("device")
        path = call.data.get("path")

        # get config entry from entity id to allow access to browser_id parameter
        entity_registry = er.async_get(hass)
        if entity := entity_registry.async_get(va_entity_id):
            entity_config_entry = hass.config_entries.async_get_entry(
                entity.config_entry_id
            )
            browser_id = entity_config_entry.data.get("browser_id")

            if browser_id:
                await browser_navigate(browser_id, path, "/view_assist/clock")

    hass.services.async_register(
        DOMAIN, "navigate", handle_navigate, schema=NAVIGATE_SERVICE_SCHEMA
    )

    async def browser_navigate(
        browser_id: str,
        path: str,
        revert_path: str | None = None,
        timeout: int = 10,
    ):
        """Navigate browser to defined view.

        Optionally revert to another view after timeout.
        """
        _LOGGER.debug("Navigating: browser_id: %s, path: %s", browser_id, path)
        await hass.services.async_call(
            "browser_mod",
            "navigate",
            {"browser_id": browser_id, "path": path},
        )

        if revert_path and timeout:
            _LOGGER.debug("Adding revert to %s in %ss", revert_path, timeout)
            hass.loop.call_later(
                10,
                partial(
                    hass.create_task,
                    browser_navigate(browser_id, revert_path),
                    f"Revert browser {browser_id}",
                ),
            )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_forward_entry_unload(
        entry, PLATFORMS
    ):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
