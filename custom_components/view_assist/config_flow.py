"""Config flow handler."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.assist_satellite import DOMAIN as ASSIST_SAT_DOMAIN
from homeassistant.components.media_player import DOMAIN as MEDIAPLAYER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_MODE, CONF_NAME, CONF_TYPE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    EntityFilterSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    BROWSERMOD_DOMAIN,
    CONF_ASSIST_PROMPT,
    CONF_BACKGROUND,
    CONF_DASHBOARD,
    CONF_DEV_MIMIC,
    CONF_DISPLAY_DEVICE,
    CONF_DO_NOT_DISTURB,
    CONF_ENABLE_MENU,
    CONF_ENABLE_MENU_TIMEOUT,
    CONF_FONT_STYLE,
    CONF_HIDE_HEADER,
    CONF_HIDE_SIDEBAR,
    CONF_HOME,
    CONF_INTENT,
    CONF_INTENT_DEVICE,
    CONF_MEDIAPLAYER_DEVICE,
    CONF_MENU_AUTO_CLOSE,
    CONF_MENU_ICON_COLOR,
    CONF_MENU_ICON_SIZE,
    CONF_MENU_ITEMS,
    CONF_MENU_TIMEOUT,
    CONF_MIC_DEVICE,
    CONF_MIC_TYPE,
    CONF_MIC_UNMUTE,
    CONF_MUSIC,
    CONF_MUSICPLAYER_DEVICE,
    CONF_ROTATE_BACKGROUND,
    CONF_ROTATE_BACKGROUND_INTERVAL,
    CONF_ROTATE_BACKGROUND_LINKED_ENTITY,
    CONF_ROTATE_BACKGROUND_PATH,
    CONF_ROTATE_BACKGROUND_SOURCE,
    CONF_STATUS_ICON_SIZE,
    CONF_STATUS_ICONS,
    CONF_USE_24H_TIME,
    CONF_USE_ANNOUNCE,
    CONF_VIEW_TIMEOUT,
    CONF_WEATHER_ENTITY,
    DEFAULT_ASSIST_PROMPT,
    DEFAULT_DASHBOARD,
    DEFAULT_DND,
    DEFAULT_ENABLE_MENU,
    DEFAULT_ENABLE_MENU_TIMEOUT,
    DEFAULT_FONT_STYLE,
    DEFAULT_HIDE_HEADER,
    DEFAULT_HIDE_SIDEBAR,
    DEFAULT_MENU_AUTO_CLOSE,
    DEFAULT_MENU_ICON_COLOR,
    DEFAULT_MENU_ICON_SIZE,
    DEFAULT_MENU_ITEMS,
    DEFAULT_MENU_TIMEOUT,
    DEFAULT_MIC_TYPE,
    DEFAULT_MIC_UNMUTE,
    DEFAULT_MODE,
    DEFAULT_NAME,
    DEFAULT_ROTATE_BACKGROUND,
    DEFAULT_ROTATE_BACKGROUND_INTERVAL,
    DEFAULT_ROTATE_BACKGROUND_PATH,
    DEFAULT_ROTATE_BACKGROUND_SOURCE,
    DEFAULT_SHOW_MENU_BUTTON,
    CONF_SHOW_MENU_BUTTON,
    DEFAULT_STATUS_ICON_SIZE,
    DEFAULT_STATUS_ICONS,
    DEFAULT_TYPE,
    DEFAULT_USE_24H_TIME,
    DEFAULT_USE_ANNOUNCE,
    DEFAULT_VIEW_BACKGROUND,
    DEFAULT_VIEW_HOME,
    DEFAULT_VIEW_INTENT,
    DEFAULT_VIEW_MUSIC,
    DEFAULT_VIEW_TIMEOUT,
    DEFAULT_WEATHER_ENITITY,
    DOMAIN,
    REMOTE_ASSIST_DISPLAY_DOMAIN,
    VAAssistPrompt,
    VAConfigEntry,
    VAIconSizes,
    VAMicType,
    VAType,
)
from .helpers import (
    get_devices_for_domain,
    get_master_config_entry,
    get_sensor_entity_from_instance,
)

_LOGGER = logging.getLogger(__name__)

BASE_SCHEMA = {
    vol.Required(CONF_NAME): str,
    vol.Required(CONF_MIC_DEVICE): EntitySelector(
        EntitySelectorConfig(domain=[SENSOR_DOMAIN, ASSIST_SAT_DOMAIN])
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
}

DISPLAY_SCHEMA = {
    vol.Required(CONF_DISPLAY_DEVICE): DeviceSelector(
        DeviceSelectorConfig(
            filter=[
                EntityFilterSelectorConfig(integration=BROWSERMOD_DOMAIN),
                EntityFilterSelectorConfig(
                    integration=REMOTE_ASSIST_DISPLAY_DOMAIN,
                ),
            ],
        )
    ),
    vol.Required(CONF_DEV_MIMIC, default=False): bool,
}


def get_display_schema(
    hass: HomeAssistant, config: VAConfigEntry | None = None
) -> dict[str, Any]:
    """Get display device options."""
    domain_filters = [BROWSERMOD_DOMAIN, REMOTE_ASSIST_DISPLAY_DOMAIN]

    hass_data = hass.data.setdefault(DOMAIN, {})
    display_devices: dict[str, Any] = hass_data.get("va_browser_ids", {})

    # Add suported domain devices
    for domain in domain_filters:
        domain_devices = get_devices_for_domain(hass, domain)
        if domain_devices:
            for device in domain_devices:
                display_devices[device.id] = device.name

    # Add current setting if not already in list
    if config is not None:
        if config.runtime_data.display_device not in display_devices:
            display_devices[config.runtime_data.display_device] = (
                config.runtime_data.display_device
            )

    # Set a dummy device for initial setup
    if not display_devices:
        display_devices = {"dummy": "dummy"}

    # Make into options dict
    options = [
        {
            "value": key,
            "label": value,
        }
        for key, value in display_devices.items()
    ]

    return (
        {
            vol.Required(CONF_DISPLAY_DEVICE): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_DEV_MIMIC, default=False): bool,
        }
        if config is None
        else {
            vol.Required(
                CONF_DISPLAY_DEVICE,
                default=config.data.get(CONF_DISPLAY_DEVICE, vol.UNDEFINED),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_DEV_MIMIC,
                default=config.data.get(CONF_DEV_MIMIC, False),
            ): bool,
        }
    )


class ViewAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for View Assist."""

    VERSION = 1
    MINOR_VERSION = 2

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
            # Include the type in the data to save in the config entry
            user_input[CONF_TYPE] = self.type
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, DEFAULT_NAME), data=user_input
            )

        # Define the schema based on the selected type
        if self.type == VAType.VIEW_AUDIO:
            data_schema = vol.Schema({**BASE_SCHEMA, **get_display_schema(self.hass)})
        else:  # audio_only
            data_schema = vol.Schema(BASE_SCHEMA)

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

        if self.va_type == VAType.VIEW_AUDIO:
            return self.async_show_menu(
                step_id="init",
                menu_options=["main_config", "dashboard_options", "default_options"],
            )
        if self.va_type == VAType.MASTER_CONFIG:
            return await self.async_step_master_config()

        return await self.async_step_main_config()

    async def async_step_master_config(self, user_input=None):
        """Handle master config flow."""
        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            options = self.config_entry.options | user_input
            return self.async_create_entry(data=options)

        data_schema = vol.Schema({})
        # Show the form for the selected type
        return self.async_show_form(step_id="master_config", data_schema=data_schema)

    async def async_step_main_config(self, user_input=None):
        """Handle main config flow."""

        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            user_input[CONF_TYPE] = self.va_type
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=user_input
            )
            return self.async_create_entry(data=None)
        # Define the schema based on the selected type
        BASE_OPTIONS = {
            vol.Required(CONF_NAME, default=self.config_entry.data[CONF_NAME]): str,
            vol.Required(
                CONF_MIC_DEVICE, default=self.config_entry.data[CONF_MIC_DEVICE]
            ): EntitySelector(
                EntitySelectorConfig(domain=[SENSOR_DOMAIN, ASSIST_SAT_DOMAIN])
            ),
            vol.Required(
                CONF_MEDIAPLAYER_DEVICE,
                default=self.config_entry.data[CONF_MEDIAPLAYER_DEVICE],
            ): EntitySelector(EntitySelectorConfig(domain=MEDIAPLAYER_DOMAIN)),
            vol.Required(
                CONF_MUSICPLAYER_DEVICE,
                default=self.config_entry.data[CONF_MUSICPLAYER_DEVICE],
            ): EntitySelector(EntitySelectorConfig(domain=MEDIAPLAYER_DOMAIN)),
            vol.Optional(
                CONF_INTENT_DEVICE,
                default=self.config_entry.data.get(CONF_INTENT_DEVICE, vol.UNDEFINED),
            ): EntitySelector(EntitySelectorConfig(domain=SENSOR_DOMAIN)),
        }

        if self.va_type == VAType.VIEW_AUDIO:
            data_schema = vol.Schema(
                {**BASE_OPTIONS, **get_display_schema(self.hass, self.config_entry)}
            )
        else:  # audio_only
            data_schema = vol.Schema(BASE_OPTIONS)

        # Show the form for the selected type
        return self.async_show_form(step_id="main_config", data_schema=data_schema)

    async def async_step_dashboard_options(self, user_input=None):
        """Handle dashboard options flow."""
        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            options = self.config_entry.options | user_input
            return self.async_create_entry(data=options)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DASHBOARD,
                    default=self.config_entry.options.get(
                        CONF_DASHBOARD, DEFAULT_DASHBOARD
                    ),
                ): str,
                vol.Optional(
                    CONF_HOME,
                    default=self.config_entry.options.get(CONF_HOME, DEFAULT_VIEW_HOME),
                ): str,
                vol.Optional(
                    CONF_MUSIC,
                    default=self.config_entry.options.get(
                        CONF_MUSIC, DEFAULT_VIEW_MUSIC
                    ),
                ): str,
                vol.Optional(
                    CONF_INTENT,
                    default=self.config_entry.options.get(
                        CONF_INTENT, DEFAULT_VIEW_INTENT
                    ),
                ): str,
                vol.Optional(
                    CONF_BACKGROUND,
                    default=self.config_entry.options.get(
                        CONF_BACKGROUND, DEFAULT_VIEW_BACKGROUND
                    ),
                ): str,
                vol.Optional(
                    CONF_ROTATE_BACKGROUND,
                    default=self.config_entry.options.get(
                        CONF_ROTATE_BACKGROUND, DEFAULT_ROTATE_BACKGROUND
                    ),
                ): bool,
                vol.Optional(
                    CONF_ROTATE_BACKGROUND_SOURCE,
                    default=self.config_entry.options.get(
                        CONF_ROTATE_BACKGROUND_SOURCE,
                        DEFAULT_ROTATE_BACKGROUND_SOURCE,
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        translation_key="rotate_backgound_source_selector",
                        options=[
                            "local_sequence",
                            "local_random",
                            "download",
                            "link_to_entity",
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(
                    CONF_ROTATE_BACKGROUND_PATH,
                    default=self.config_entry.options.get(
                        CONF_ROTATE_BACKGROUND_PATH,
                        DEFAULT_ROTATE_BACKGROUND_PATH,
                    ),
                ): str,
                vol.Optional(
                    CONF_ROTATE_BACKGROUND_LINKED_ENTITY,
                    default=self.config_entry.options.get(
                        CONF_ROTATE_BACKGROUND_LINKED_ENTITY, vol.UNDEFINED
                    ),
                ): EntitySelector(
                    EntitySelectorConfig(
                        integration=DOMAIN,
                        domain=SENSOR_DOMAIN,
                        exclude_entities=[
                            get_sensor_entity_from_instance(
                                self.hass, self.config_entry.entry_id
                            )
                        ],
                    )
                ),
                vol.Optional(
                    CONF_ROTATE_BACKGROUND_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_ROTATE_BACKGROUND_INTERVAL,
                        DEFAULT_ROTATE_BACKGROUND_INTERVAL,
                    ),
                ): int,
                vol.Optional(
                    CONF_ASSIST_PROMPT,
                    default=self.config_entry.options.get(
                        CONF_ASSIST_PROMPT, DEFAULT_ASSIST_PROMPT
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        translation_key="assist_prompt_selector",
                        options=[e.value for e in VAAssistPrompt],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_STATUS_ICON_SIZE,
                    default=self.config_entry.options.get(
                        CONF_STATUS_ICON_SIZE, DEFAULT_STATUS_ICON_SIZE
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        translation_key="status_icons_size_selector",
                        options=[e.value for e in VAIconSizes],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_FONT_STYLE,
                    default=self.config_entry.options.get(
                        CONF_FONT_STYLE, DEFAULT_FONT_STYLE
                    ),
                ): str,
                vol.Optional(
                    CONF_STATUS_ICONS,
                    default=self.config_entry.options.get(
                        CONF_STATUS_ICONS, DEFAULT_STATUS_ICONS
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        translation_key="status_icons_selector",
                        options=[],
                        mode=SelectSelectorMode.LIST,
                        multiple=True,
                        custom_value=True,
                    )
                ),
                vol.Optional(
                    CONF_USE_24H_TIME,
                    default=self.config_entry.options.get(
                        CONF_USE_24H_TIME, DEFAULT_USE_24H_TIME
                    ),
                ): bool,
                vol.Optional(
                    CONF_HIDE_SIDEBAR,
                    default=self.config_entry.options.get(
                        CONF_HIDE_SIDEBAR, DEFAULT_HIDE_SIDEBAR
                    ),
                ): bool,
                vol.Optional(
                    CONF_HIDE_HEADER,
                    default=self.config_entry.options.get(
                        CONF_HIDE_HEADER, DEFAULT_HIDE_HEADER
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_MENU,
                    default=self.config_entry.options.get(
                        CONF_ENABLE_MENU, DEFAULT_ENABLE_MENU
                    ),
                ): bool,
                vol.Optional(
                    CONF_MENU_ITEMS,
                    default=self.config_entry.options.get(
                        CONF_MENU_ITEMS, DEFAULT_MENU_ITEMS
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        translation_key="menu_items_selector",
                        options=[],
                        mode=SelectSelectorMode.LIST,
                        multiple=True,
                        custom_value=True,
                    )
                ),
                vol.Optional(
                    CONF_ENABLE_MENU_TIMEOUT,
                    default=self.config_entry.options.get(
                        CONF_ENABLE_MENU_TIMEOUT, DEFAULT_ENABLE_MENU_TIMEOUT
                    ),
                ): bool,
                vol.Optional(
                    CONF_MENU_TIMEOUT,
                    default=self.config_entry.options.get(
                        CONF_MENU_TIMEOUT, DEFAULT_MENU_TIMEOUT
                    ),
                ): int,
                vol.Optional(
                    CONF_SHOW_MENU_BUTTON,
                    default=self.config_entry.options.get(
                        CONF_SHOW_MENU_BUTTON, DEFAULT_SHOW_MENU_BUTTON
                    ),
                ): bool,
                vol.Optional(
                    CONF_MENU_ICON_SIZE,
                    default=self.config_entry.options.get(
                        CONF_MENU_ICON_SIZE, DEFAULT_MENU_ICON_SIZE
                    ),
                ): str,
                vol.Optional(
                    CONF_MENU_ICON_COLOR,
                    default=self.config_entry.options.get(
                        CONF_MENU_ICON_COLOR, DEFAULT_MENU_ICON_COLOR
                    ),
                ): str,
            }
        )

        # Show the form for the selected type
        return self.async_show_form(
            step_id="dashboard_options", data_schema=data_schema
        )

    async def async_step_default_options(self, user_input=None):
        """Handle default options flow."""
        if user_input is not None:
            # This is just updating the core config so update config_entry.data
            options = self.config_entry.options | user_input
            return self.async_create_entry(data=options)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_WEATHER_ENTITY,
                    default=self.config_entry.options.get(
                        CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENITITY
                    ),
                ): EntitySelector(EntitySelectorConfig(domain=WEATHER_DOMAIN)),
                vol.Optional(
                    CONF_MIC_TYPE,
                    default=self.config_entry.options.get(
                        CONF_MIC_TYPE, DEFAULT_MIC_TYPE
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        translation_key="mic_type_selector",
                        options=[e.value for e in VAMicType],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_MODE,
                    default=self.config_entry.options.get(CONF_MODE, DEFAULT_MODE),
                ): str,
                vol.Optional(
                    CONF_VIEW_TIMEOUT,
                    default=self.config_entry.options.get(
                        CONF_VIEW_TIMEOUT, DEFAULT_VIEW_TIMEOUT
                    ),
                ): int,
                vol.Optional(
                    CONF_DO_NOT_DISTURB,
                    default=self.config_entry.options.get(
                        CONF_DO_NOT_DISTURB, DEFAULT_DND
                    ),
                ): bool,
                vol.Optional(
                    CONF_USE_ANNOUNCE,
                    default=self.config_entry.options.get(
                        CONF_USE_ANNOUNCE, DEFAULT_USE_ANNOUNCE
                    ),
                ): bool,
                vol.Optional(
                    CONF_MIC_UNMUTE,
                    default=self.config_entry.options.get(
                        CONF_MIC_UNMUTE, DEFAULT_MIC_UNMUTE
                    ),
                ): bool,
            }
        )

        # Show the form for the selected type
        return self.async_show_form(step_id="default_options", data_schema=data_schema)
