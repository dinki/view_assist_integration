"""Functions to configure Lovelace frontend with dashboard and views."""

import logging

from homeassistant.components import frontend
from homeassistant.components.lovelace import (
    CONF_ICON,
    CONF_SHOW_IN_SIDEBAR,
    CONF_TITLE,
    CONF_URL_PATH,
    LovelaceData,
    dashboard,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import load_yaml_dict

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DASHBOARD_NAME = "View Assist"

# This is path of dashboard files in integration directory.
# Ie the directory this file is in.
CONFIG_FILES_PATH = "default_config"

# This could be replaced with a function that loads all files in directory if desired
VIEWS_TO_LOAD = ["clock", "music", "info", "weather"]


class FrontendConfig:
    """Class to configure front end for View Assist."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        self.hass = hass
        self.files_path = f"{self.hass.config.config_dir}/custom_components/{DOMAIN}/{CONFIG_FILES_PATH}"
        self.path = DASHBOARD_NAME.replace(" ", "-").lower()

    async def async_config(self):
        """Create the view assist dashboard and views if they dont exist already.

        It will not overwrite modifications made to views
        """
        await self._config_dashboard()
        await self._config_views(VIEWS_TO_LOAD)
        await self._delete_home_view()

    async def _config_dashboard(self):
        """Create dashboard."""

        # Get lovelace (frontend) config data
        lovelace: LovelaceData = self.hass.data["lovelace"]

        # Path to dashboard config file
        f = f"{self.files_path}/dashboard.yaml"
        dashboard_config = await self.hass.async_add_executor_job(load_yaml_dict, f)
        dashboard_config["mode"] = "storage"

        dc = dashboard.DashboardsCollection(self.hass)
        await dc.async_load()
        dashboard_exists = any(e["url_path"] == self.path for e in dc.async_items())

        if not dashboard_exists:
            # Create entry in dashboard collection
            await dc.async_create_item(
                {
                    CONF_ICON: "mdi:glasses",
                    CONF_TITLE: DASHBOARD_NAME,
                    CONF_URL_PATH: self.path,
                    CONF_SHOW_IN_SIDEBAR: True,
                }
            )

            # Add dashboard entry to Lovelace storage
            lovelace.dashboards[self.path] = dashboard.LovelaceStorage(
                self.hass,
                {
                    "id": "view_assist",
                    CONF_ICON: "mdi:glasses",
                    CONF_TITLE: DASHBOARD_NAME,
                    CONF_URL_PATH: self.path,
                },
            )
            await lovelace.dashboards[self.path].async_save(dashboard_config)

            # Register panel
            kwargs = {
                "frontend_url_path": self.path,
                "require_admin": False,
                "config": dashboard_config,
                "sidebar_title": DASHBOARD_NAME,
                "sidebar_icon": "mdi:glasses",
            }
            frontend.async_register_built_in_panel(
                self.hass, "lovelace", **kwargs, update=False
            )

            await dc.async_update_item("view_assist", {CONF_SHOW_IN_SIDEBAR: True})

            # TODO: Needs restart for lovelace to properly manage dashboard.  Must be some
            # event that needs raising.

    async def _config_views(self, views_to_load: list[str]):
        """Create views from config files if not exist."""
        # Get lovelace (frontend) config data
        lovelace: LovelaceData = self.hass.data["lovelace"]

        # Get access to dashboard store
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(self.path)

        # Load dashboard config data
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)

            # Make list of existing view names for this dashboard
            existing_views = [
                view["path"] for view in dashboard_config["views"] if view.get("path")
            ]

            # Iterate list of views to add
            modified = False
            for view in views_to_load:
                # If view already exists, skip adding it
                if view in existing_views:
                    continue

                # Load view config from file.
                f = f"{self.files_path}/views/{view}.yaml"
                new_view_config = await self.hass.async_add_executor_job(
                    load_yaml_dict, f
                )

                # Create new view and add it to dashboard
                new_view = {
                    "type": "panel",
                    "title": view.title(),
                    "path": view,
                    "cards": [new_view_config],
                }

                dashboard_config["views"].append(new_view)
                modified = True

            # Save dashboard config back to HA
            if modified:
                await dashboard_store.async_save(dashboard_config)

    async def _delete_home_view(self):
        # Get lovelace (frontend) config data
        lovelace = self.hass.data["lovelace"]

        # Get access to dashboard store
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(self.path)

        # Load dashboard config data
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(True)

            # Remove view with title of home
            modified = False
            for i, view in enumerate(dashboard_config["views"]):
                if view.get("title", "").lower() == "home":
                    del dashboard_config["views"][i]
                    modified = True
                    break

            # Save dashboard config back to HA
            if modified:
                await dashboard_store.async_save(dashboard_config)
