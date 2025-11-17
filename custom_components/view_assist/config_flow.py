"""Config flow handler."""

import logging
from typing import Any

from awesomeversion import AwesomeVersion
import voluptuous as vol

from homeassistant.components.assist_satellite import DOMAIN as ASSIST_SAT_DOMAIN
from homeassistant.components.media_player import DOMAIN as MEDIAPLAYER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_DEVICE, CONF_MODE, CONF_NAME, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import SectionConfig, section
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    ConversationAgentSelector,
    ConversationAgentSelectorConfig,
    DeviceSelector,
    DeviceSelectorConfig,
    EntityFilterSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .assets import AssetClass, AssetsManager
from .const import (
    CONF_ASSIST_PROMPT,
    CONF_BACKGROUND,
    CONF_BACKGROUND_MODE,
    CONF_BACKGROUND_SETTINGS,
    CONF_DASHBOARD,
    CONF_DEVELOPER_DEVICE,
    CONF_DEVELOPER_MIMIC_DEVICE,
    CONF_DISPLAY_DEVICE,
    CONF_DISPLAY_SETTINGS,
    CONF_DO_NOT_DISTURB,
    CONF_DUCKING_VOLUME,
    CONF_ENABLE_UPDATES,
    CONF_FONT_STYLE,
    CONF_HOME,
    CONF_INTENT,
    CONF_INTENT_DEVICE,
    CONF_LIST,
    CONF_MEDIAPLAYER_DEVICE,
    CONF_MENU_CONFIG,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    CONF_MIC_DEVICE,
    CONF_MIC_UNMUTE,
    CONF_MUSIC,
    CONF_MUSICPLAYER_DEVICE,
    CONF_ORIENTATION_SENSOR,
    CONF_ROTATE_BACKGROUND_INTERVAL,
    CONF_ROTATE_BACKGROUND_LINKED_ENTITY,
    CONF_ROTATE_BACKGROUND_PATH,
    CONF_SCREEN_MODE,
    CONF_STATUS_ICON_SIZE,
    CONF_STATUS_ICONS,
    CONF_TIME_FORMAT,
    CONF_TRANSLATION_ENGINE,
    CONF_USE_ANNOUNCE,
    CONF_VIEW_TIMEOUT,
    CONF_WEATHER_ENTITY,
    DEFAULT_NAME,
    DEFAULT_TYPE,
    DEFAULT_VALUES,
    DOMAIN,
    MIN_DASHBOARD_FOR_OVERLAYS,
    VACA_DOMAIN,
    VAIconSizes,
)
from .helpers import get_available_overlays, get_master_config_entry
from .typed import (
    DISPLAY_DEVICE_TYPES,
    VAAssistPrompt,
    VABackgroundMode,
    VAConfigEntry,
    VAMenuConfig,
    VAScreenMode,
    VATimeFormat,
    VAType,
)

_LOGGER = logging.getLogger(__name__)

MASTER_FORM_DESCRIPTION = "Values here will be used when no value is set on the View Assist satellite device configuration"
DEVICE_FORM_DESCRIPTION = (
    "Setting values here will override the master config settings for this device"
)

BASE_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_MIC_DEVICE): EntitySelector(
            EntitySelectorConfig(
                filter=[
                    EntityFilterSelectorConfig(
                        integration="esphome", domain=ASSIST_SAT_DOMAIN
                    ),
                    EntityFilterSelectorConfig(
                        integration="hassmic", domain=[SENSOR_DOMAIN, ASSIST_SAT_DOMAIN]
                    ),
                    EntityFilterSelectorConfig(
                        integration="stream_assist",
                        domain=[SENSOR_DOMAIN, ASSIST_SAT_DOMAIN],
                    ),
                    EntityFilterSelectorConfig(
                        integration="wyoming", domain=ASSIST_SAT_DOMAIN
                    ),
                    EntityFilterSelectorConfig(
                        integration=VACA_DOMAIN, domain=ASSIST_SAT_DOMAIN
                    ),
                ]
            )
        ),
        vol.Required(CONF_MEDIAPLAYER_DEVICE): EntitySelector(
            EntitySelectorConfig(domain=MEDIAPLAYER_DOMAIN)
        ),
        vol.Required(CONF_MUSICPLAYER_DEVICE): EntitySelector(
            EntitySelectorConfig(domain=MEDIAPLAYER_DOMAIN)
        ),
        vol.Optional(CONF_INTENT_DEVICE, default=vol.UNDEFINED): EntitySelector(
            EntitySelectorConfig(domain=SENSOR_DOMAIN)
        ),
        vol.Optional(CONF_ORIENTATION_SENSOR, default=vol.UNDEFINED): EntitySelector(
            EntitySelectorConfig(domain=SENSOR_DOMAIN)
        ),
    }
)


def get_vaca_config(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    """Get the config for a VACA device."""

    def get_entity(
        domain: str, suffix: str, entities: list[er.RegistryEntry]
    ) -> str | None:
        for entity in entities:
            if entity.entity_id.startswith(f"{domain}."):
                if (not suffix) or (suffix and entity.entity_id.endswith(f"_{suffix}")):
                    return entity.entity_id
        return None

    def get_display_id(device_id: str) -> str | None:
        device_registry = dr.async_get(hass)
        if device := device_registry.async_get(device_id):
            return f"va-{device.name.split(' ')[1]}"
        return None

    entity_registry = er.async_get(hass)
    entities = er.async_entries_for_device(entity_registry, device_id)

    return {
        CONF_MIC_DEVICE: get_entity("assist_satellite", "", entities) or "",
        CONF_MEDIAPLAYER_DEVICE: get_entity("media_player", "", entities) or "",
        CONF_MUSICPLAYER_DEVICE: get_entity("media_player", "", entities) or "",
        CONF_INTENT_DEVICE: get_entity("sensor", "intent", entities) or "",
        CONF_DISPLAY_DEVICE: get_display_id(device_id) or "",
        CONF_ORIENTATION_SENSOR: get_entity("sensor", "orientation", entities) or "",
    }


def get_display_devices(
    hass: HomeAssistant, config: VAConfigEntry | None = None
) -> dict[str, Any]:
    """Get display device options."""

    # Make into options dict
    return [
        {
            "value": key,
            "label": value,
        }
        for key, value in hass.data[DOMAIN].get("browser_ids", {}).items()
    ]


async def get_dashboard_options_schema(
    hass: HomeAssistant, config_entry: VAConfigEntry | None
) -> vol.Schema:
    """Return schema for dashboard options."""
    is_master = (
        config_entry is not None
        and config_entry.data[CONF_TYPE] == VAType.MASTER_CONFIG
    )

    # Modify any option lists
    if is_master:
        background_source_options = [
            e.value for e in VABackgroundMode if e != VABackgroundMode.LINKED
        ]
        background_extra = {}
    else:
        background_source_options = [e.value for e in VABackgroundMode]
        background_extra = {
            vol.Optional(CONF_ROTATE_BACKGROUND_LINKED_ENTITY): (
                EntitySelector(
                    EntitySelectorConfig(
                        integration=DOMAIN,
                        domain=SENSOR_DOMAIN,
                        exclude_entities=[],
                    )
                )
            )
        }

    # Get the overlay options
    installed_dashboard = await AssetsManager.get(hass).get_installed_version(
        AssetClass.DASHBOARD, "dashboard"
    )
    if AwesomeVersion(installed_dashboard) >= MIN_DASHBOARD_FOR_OVERLAYS:
        available_overlays = await hass.async_add_executor_job(
            get_available_overlays, hass
        )
        overlay_options = [
            {"value": key, "label": value} for key, value in available_overlays.items()
        ]
    else:
        _LOGGER.debug("No overlays available, using default options")
        overlay_options = [e.value for e in VAAssistPrompt]

    BASE = {
        vol.Optional(CONF_DASHBOARD): str,
        vol.Optional(CONF_HOME): str,
        vol.Optional(CONF_MUSIC): str,
        vol.Optional(CONF_INTENT): str,
        vol.Optional(CONF_LIST): str,
    }
    BACKGROUND_SETTINGS = {
        vol.Optional(CONF_BACKGROUND_MODE): SelectSelector(
            SelectSelectorConfig(
                translation_key="rotate_backgound_source_selector",
                options=background_source_options,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_BACKGROUND): str,
        vol.Optional(CONF_ROTATE_BACKGROUND_PATH): str,
        vol.Optional(CONF_ROTATE_BACKGROUND_INTERVAL): int,
    }

    DISPLAY_SETTINGS = {
        vol.Optional(CONF_ASSIST_PROMPT): SelectSelector(
            SelectSelectorConfig(
                translation_key="assist_prompt_selector",
                options=overlay_options,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_FONT_STYLE): str,
        vol.Optional(CONF_STATUS_ICON_SIZE): SelectSelector(
            SelectSelectorConfig(
                translation_key="status_icons_size_selector",
                options=[e.value for e in VAIconSizes],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_STATUS_ICONS): SelectSelector(
            SelectSelectorConfig(
                translation_key="status_icons_selector",
                options=[],
                mode=SelectSelectorMode.LIST,
                multiple=True,
                custom_value=True,
            )
        ),
        vol.Optional(CONF_MENU_CONFIG): SelectSelector(
            SelectSelectorConfig(
                translation_key="menu_config_selector",
                options=[e.value for e in VAMenuConfig],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_MENU_ITEMS): SelectSelector(
            SelectSelectorConfig(
                translation_key="menu_icons_selector",
                options=[],
                mode=SelectSelectorMode.LIST,
                multiple=True,
                custom_value=True,
            )
        ),
        vol.Optional(CONF_MENU_TIMEOUT): int,
        vol.Optional(CONF_TIME_FORMAT): SelectSelector(
            SelectSelectorConfig(
                options=[e.value for e in VATimeFormat],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="lookup_selector",
            )
        ),
        vol.Optional(CONF_SCREEN_MODE): SelectSelector(
            SelectSelectorConfig(
                options=[e.value for e in VAScreenMode],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="lookup_selector",
            )
        ),
    }

    BACKGROUND_SETTINGS.update(background_extra)

    schema = BASE
    schema[vol.Required(CONF_BACKGROUND_SETTINGS)] = section(
        vol.Schema(BACKGROUND_SETTINGS), options=SectionConfig(collapsed=True)
    )
    schema[vol.Required(CONF_DISPLAY_SETTINGS)] = section(
        vol.Schema(DISPLAY_SETTINGS), options=SectionConfig(collapsed=True)
    )
    return vol.Schema(schema)


DEFAULT_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_WEATHER_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=WEATHER_DOMAIN)
        ),
        vol.Optional(CONF_MODE): str,
        vol.Optional(CONF_VIEW_TIMEOUT): NumberSelector(
            NumberSelectorConfig(min=5, max=999, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(CONF_DO_NOT_DISTURB): SelectSelector(
            SelectSelectorConfig(
                options=["on", "off"],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="lookup_selector",
            )
        ),
        vol.Optional(CONF_USE_ANNOUNCE): SelectSelector(
            SelectSelectorConfig(
                options=["on", "off"],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="lookup_selector",
            )
        ),
        vol.Optional(CONF_MIC_UNMUTE): SelectSelector(
            SelectSelectorConfig(
                options=["on", "off"],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="lookup_selector",
            )
        ),
        vol.Optional(CONF_DUCKING_VOLUME): NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=100,
                step=1.0,
                mode=NumberSelectorMode.BOX,
            )
        ),
    }
)

INTEGRATION_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ENABLE_UPDATES): BooleanSelector(),
        vol.Optional(CONF_TRANSLATION_ENGINE): ConversationAgentSelector(),
    }
)


def get_developer_options_schema(
    hass: HomeAssistant, config_entry: VAConfigEntry | None
) -> vol.Schema:
    """Return schema for dashboard options."""
    return vol.Schema(
        {
            vol.Optional(CONF_DEVELOPER_DEVICE): SelectSelector(
                SelectSelectorConfig(
                    options=get_display_devices(hass, config_entry),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_DEVELOPER_MIMIC_DEVICE): EntitySelector(
                EntitySelectorConfig(integration=DOMAIN, domain=Platform.SENSOR)
            ),
        }
    )


def get_suggested_option_values(config: VAConfigEntry) -> dict[str, Any]:
    """Get suggested values for the config entry."""
    if config.data[CONF_TYPE] == VAType.MASTER_CONFIG:
        option_values = DEFAULT_VALUES.copy()
        for option in DEFAULT_VALUES:
            if config.options.get(option) is not None:
                option_values[option] = config.options.get(option)
        return option_values
    return config.options


class ViewAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for View Assist."""

    VERSION = 1
    MINOR_VERSION = 5

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler.

        Remove this method and the ExampleOptionsFlowHandler class
        if you do not want any options for your integration.
        """
        return ViewAssistOptionsFlowHandler()

    def __init__(self) -> None:
        """Initialise."""
        super().__init__()
        self.type = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            self.type = user_input[CONF_TYPE]
            return await self.async_step_options()

        # Show the initial form to select the type with descriptive text
        if get_master_config_entry(self.hass):
            return self.async_show_form(
                step_id="user",
                last_step=False,
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_TYPE, default=DEFAULT_TYPE): SelectSelector(
                            SelectSelectorConfig(
                                translation_key="type_selector",
                                options=[
                                    e.value for e in VAType if e != VAType.MASTER_CONFIG
                                ],
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        ),
                    }
                ),
            )

        return self.async_show_form(step_id="master_config", last_step=True)

    async def async_step_integration_discovery(self, discovery_info=None):
        """Handle the master config integration discovery step.

        This is called from init.py if no master config instance exists
        """
        if discovery_info.get(CONF_NAME) != VAType.MASTER_CONFIG:
            return self.async_abort(reason="wrong integration")

        await self.async_set_unique_id(f"{DOMAIN}_{VAType.MASTER_CONFIG}")
        self._abort_if_unique_id_configured()

        self.context.update({"title_placeholders": {"name": "Master Configuration"}})
        return await self.async_step_master_config()

    async def async_step_options(self, user_input=None):
        """Handle the options step."""
        if user_input is not None:
            # Auto populate config parameters if VACA device
            if self.type == VAType.VACA:
                device_id = user_input.pop(CONF_DEVICE)
                user_input = user_input | get_vaca_config(self.hass, device_id)

            # Include the type in the data to save in the config entry
            user_input[CONF_TYPE] = self.type

            return self.async_create_entry(
                title=user_input.get(CONF_NAME, DEFAULT_NAME), data=user_input
            )

        # Define the schema based on the selected type
        if self.type == VAType.VACA:
            # Special simple VACA setup
            data_schema = vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_DEVICE): DeviceSelector(
                        DeviceSelectorConfig(
                            integration=VACA_DOMAIN,
                        )
                    ),
                }
            )
        elif self.type == VAType.VIEW_AUDIO:
            data_schema = BASE_DEVICE_SCHEMA.extend(
                {
                    vol.Required(CONF_DISPLAY_DEVICE): SelectSelector(
                        SelectSelectorConfig(
                            options=get_display_devices(self.hass),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            )
        else:  # audio_only
            data_schema = BASE_DEVICE_SCHEMA

        # Show the form for the selected type
        return self.async_show_form(step_id="options", data_schema=data_schema)

    async def async_step_master_config(self, discovery_info=None):
        """Handle the options step."""
        if discovery_info is not None and not get_master_config_entry(self.hass):
            return self.async_create_entry(
                title="Master Configuration", data={"type": VAType.MASTER_CONFIG}
            )
        return self.async_show_form(
            step_id="master_config",
            data_schema=vol.Schema({}),
        )


class ViewAssistOptionsFlowHandler(OptionsFlow):
    """Handles the options flow.

    Here we use an initial menu to select different options forms,
    and show how to use api data to populate a selector.
    """

    async def async_step_init(self, user_input=None):
        """Handle options flow."""

        # Display an options menu if display device
        # Display reconfigure form if audio only

        # Also need to be in strings.json and translation files.
        self.va_type = self.config_entry.data[CONF_TYPE]  # pylint: disable=attribute-defined-outside-init

        if self.va_type in DISPLAY_DEVICE_TYPES:
            return self.async_show_menu(
                step_id="init",
                menu_options=["main_config", "dashboard_options", "default_options"],
            )
        if self.va_type == VAType.MASTER_CONFIG:
            return self.async_show_menu(
                step_id="init",
                menu_options=[
                    "integration_options",
                    "dashboard_options",
                    "default_options",
                    "developer_options",
                ],
            )

        return await self.async_step_main_config()

    async def async_step_main_config(self, user_input=None):
        """Handle main config flow."""

        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            user_input[CONF_TYPE] = self.va_type
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=user_input
            )
            return self.async_create_entry(data=None)

        if self.va_type in DISPLAY_DEVICE_TYPES:
            data_schema = BASE_DEVICE_SCHEMA.extend(
                {
                    vol.Required(CONF_DISPLAY_DEVICE): SelectSelector(
                        SelectSelectorConfig(
                            options=get_display_devices(self.hass, self.config_entry),
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            )
            data_schema = self.add_suggested_values_to_schema(
                data_schema, self.config_entry.data
            )
        else:  # audio_only
            data_schema = self.add_suggested_values_to_schema(
                BASE_DEVICE_SCHEMA, self.config_entry.data
            )

        # Show the form for the selected type
        return self.async_show_form(
            step_id="main_config",
            data_schema=data_schema,
            description_placeholders={"name": self.config_entry.title},
        )

    async def async_step_dashboard_options(self, user_input=None):
        """Handle dashboard options flow."""
        data_schema = self.add_suggested_values_to_schema(
            await get_dashboard_options_schema(self.hass, self.config_entry),
            get_suggested_option_values(self.config_entry),
        )

        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            options = self.config_entry.options | user_input
            for o in data_schema.schema:
                if o not in user_input:
                    options.pop(o, None)
            return self.async_create_entry(data=options)

        # Show the form
        return self.async_show_form(
            step_id="dashboard_options",
            data_schema=data_schema,
            description_placeholders={
                "name": self.config_entry.title,
                "description": MASTER_FORM_DESCRIPTION
                if self.config_entry.data[CONF_TYPE] == VAType.MASTER_CONFIG
                else DEVICE_FORM_DESCRIPTION,
            },
        )

    async def async_step_default_options(self, user_input=None):
        """Handle default options flow."""

        data_schema = self.add_suggested_values_to_schema(
            DEFAULT_OPTIONS_SCHEMA, get_suggested_option_values(self.config_entry)
        )

        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            options = self.config_entry.options | user_input
            for o in data_schema.schema:
                if o not in user_input:
                    options.pop(o, None)
            return self.async_create_entry(data=options)

        # Show the form
        return self.async_show_form(
            step_id="default_options",
            data_schema=data_schema,
            description_placeholders={
                "name": self.config_entry.title,
                "description": MASTER_FORM_DESCRIPTION
                if self.config_entry.data[CONF_TYPE] == VAType.MASTER_CONFIG
                else DEVICE_FORM_DESCRIPTION,
            },
        )

    async def async_step_integration_options(self, user_input=None):
        """Handle integration options flow."""

        data_schema = self.add_suggested_values_to_schema(
            INTEGRATION_OPTIONS_SCHEMA,
            {
                CONF_ENABLE_UPDATES: self.config_entry.options.get(CONF_ENABLE_UPDATES),
                CONF_TRANSLATION_ENGINE: self.config_entry.options.get(
                    CONF_TRANSLATION_ENGINE
                ),
            },
        )

        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            options = self.config_entry.options | user_input
            for o in data_schema.schema:
                if o not in user_input:
                    options.pop(o, None)
            return self.async_create_entry(data=options)

        # Show the form
        return self.async_show_form(
            step_id="integration_options",
            data_schema=data_schema,
            description_placeholders={"name": self.config_entry.title},
        )

    async def async_step_developer_options(self, user_input=None):
        """Handle default options flow."""

        data_schema = self.add_suggested_values_to_schema(
            get_developer_options_schema(self.hass, self.config_entry),
            get_suggested_option_values(self.config_entry),
        )

        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            options = self.config_entry.options | user_input
            for o in data_schema.schema:
                if o not in user_input:
                    options.pop(o, None)
            return self.async_create_entry(data=options)

        # Show the form
        return self.async_show_form(
            step_id="developer_options",
            data_schema=data_schema,
            description_placeholders={"name": self.config_entry.title},
        )
