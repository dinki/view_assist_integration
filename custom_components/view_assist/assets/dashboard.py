"""Assets manager for dashboard."""

from __future__ import annotations

import logging
import operator
from pathlib import Path
from typing import Any

from homeassistant.components.lovelace import (
    CONF_ICON,
    CONF_REQUIRE_ADMIN,
    CONF_SHOW_IN_SIDEBAR,
    CONF_TITLE,
    CONF_URL_PATH,
    LovelaceData,
    dashboard,
)
from homeassistant.const import CONF_ID, CONF_MODE, CONF_TYPE, EVENT_LOVELACE_UPDATED
from homeassistant.core import Event, HomeAssistant
from homeassistant.util.yaml import load_yaml_dict, parse_yaml, save_yaml

from ..const import (  # noqa: TID252
    DASHBOARD_DIR,
    DASHBOARD_NAME,
    DASHBOARD_VIEWS_GITHUB_PATH,
    DOMAIN,
    GITHUB_BRANCH,
    GITHUB_DEV_BRANCH,
)
from ..helpers import differ_to_json, get_key, json_to_dictdiffer  # noqa: TID252
from ..typed import VAConfigEntry  # noqa: TID252
from ..utils import dictdiff  # noqa: TID252
from ..websocket import MockWSConnection  # noqa: TID252
from .base import AssetManagerException, BaseAssetManager, InstallStatus

_LOGGER = logging.getLogger(__name__)


class DashboardManager(BaseAssetManager):
    """Class to manage dashboard assets."""

    def __init__(
        self, hass: HomeAssistant, config: VAConfigEntry, data: dict[str, Any]
    ) -> None:
        """Initialise."""
        super().__init__(hass, config, data)
        self.ignore_change_events = False

    async def async_setup(self) -> None:
        """Set up the AssetManager."""

        # Experimental - listen for dashboard change and write out changes
        self.config.async_on_unload(
            self.hass.bus.async_listen(EVENT_LOVELACE_UPDATED, self._dashboard_changed)
        )

    async def async_onboard(self, force: bool = False) -> dict[str, Any] | None:
        """Onboard the user if not yet setup."""
        name = "dashboard"
        db_version = {}

        if self.is_installed(name):
            # Ensure dashboard file exists
            await self._download_dashboard(cancel_if_exists=True)

            # Update user-dashboard diff file
            await self._dashboard_changed(
                Event("lovelace_updated", {"url_path": self._dashboard_key})
            )

            # Migration to update management of already installed dashboard
            installed_version = await self.async_get_installed_version(name)
            latest_version = await self.async_get_latest_version(name)
            _LOGGER.debug(
                "Dashboard already installed.  Registering version - %s",
                installed_version,
            )
            db_version[name] = {
                "installed": installed_version,
                "latest": latest_version,
            }
            return db_version

        self.onboarding = True
        # Check if onboarding is needed and if so, run it
        _LOGGER.debug("Installing dashboard")
        self.ignore_change_events = True
        status = {}
        result = await self.async_install_or_update(
            name=DASHBOARD_NAME,
            download=True,
            backup_existing=False,
        )
        if result.installed:
            db_version[name] = {
                "installed": result.version,
                "latest": result.latest_version,
            }
        self.ignore_change_events = False
        self.onboarding = False

        return status

    async def async_get_last_commit(self) -> str | None:
        """Get if the repo has a new update."""
        return await self.download_manager.get_last_commit_id(
            f"{DASHBOARD_VIEWS_GITHUB_PATH}/{DASHBOARD_DIR}"
        )

    async def async_get_installed_version(self, name: str) -> str | None:
        """Get installed version of asset."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )
        # Load dashboard config data
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)
            return self._read_dashboard_version(dashboard_config)
        return None

    async def async_get_latest_version(self, name: str) -> dict[str, Any]:
        """Get latest version of asset from repo."""
        dashboard_path = f"{DASHBOARD_VIEWS_GITHUB_PATH}/{DASHBOARD_DIR}/{name}.yaml"
        dashboard_data = await self.download_manager.get_file_contents(dashboard_path)
        # Parse yaml string to json
        dashboard_data = parse_yaml(dashboard_data)
        return self._read_dashboard_version(dashboard_data)

    async def async_get_version_info(
        self, update_from_repo: bool = True
    ) -> dict[str, Any]:
        """Get dashboard version from repo."""
        return {
            "dashboard": {
                "installed": await self.async_get_installed_version(DASHBOARD_DIR),
                "latest": await self.async_get_latest_version(DASHBOARD_DIR)
                if update_from_repo
                else self.data.get("dashboard", {}).get("latest"),
            }
        }

    def is_installed(self, name: str) -> bool:
        """Return blueprint exists."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        return self._dashboard_key in lovelace.dashboards

    async def async_install_or_update(
        self,
        name: str,
        download: bool = False,
        dev_branch: bool = False,
        discard_user_dashboard_changes: bool = False,
        backup_existing: bool = False,
    ) -> InstallStatus:
        """Install or update dashboard."""
        success = False
        installed = self.is_installed("dashboard")

        _LOGGER.debug("%s dashboard", "Updating" if installed else "Adding")

        self._update_install_progress("dashboard", 10)

        # Download view if required
        downloaded = False
        if download:
            # Download dashboard
            _LOGGER.debug("Downloading dashboard")
            # Set branch to download from
            if dev_branch:
                self.download_manager.set_branch(GITHUB_DEV_BRANCH)
            else:
                self.download_manager.set_branch(GITHUB_BRANCH)

            downloaded = await self._download_dashboard()
            if not downloaded:
                raise AssetManagerException("Unable to download dashboard")

        self._update_install_progress("dashboard", 50)

        dashboard_file_path = Path(
            self.hass.config.path(DOMAIN),
            f"{DASHBOARD_DIR}/{DASHBOARD_DIR}.yaml",
        )
        if not Path(dashboard_file_path).exists():
            # No dashboard file
            raise AssetManagerException(
                f"Dashboard file not found: {dashboard_file_path}"
            )

        # Ignore change events during update/install
        self.ignore_change_events = True

        if not self.is_installed(self._dashboard_key):
            _LOGGER.debug("Installing dashboard")
            mock_connection = MockWSConnection(self.hass)
            if mock_connection.execute_ws_func(
                "lovelace/dashboards/create",
                {
                    CONF_ID: 1,
                    CONF_TYPE: "lovelace/dashboards/create",
                    CONF_ICON: "mdi:glasses",
                    CONF_TITLE: DASHBOARD_NAME,
                    CONF_URL_PATH: self._dashboard_key,
                    CONF_MODE: "storage",
                    CONF_SHOW_IN_SIDEBAR: True,
                    CONF_REQUIRE_ADMIN: False,
                },
            ):
                # Get lovelace (frontend) config data
                lovelace: LovelaceData = self.hass.data["lovelace"]

                # Load dashboard config file from path
                dashboard_file_path = Path(
                    self.hass.config.path(DOMAIN),
                    f"{DASHBOARD_DIR}/{DASHBOARD_DIR}.yaml",
                )

                if new_dashboard_config := await self.hass.async_add_executor_job(
                    load_yaml_dict, dashboard_file_path
                ):
                    self._update_install_progress("dashboard", 70)
                    await lovelace.dashboards[self._dashboard_key].async_save(
                        new_dashboard_config
                    )
                    self._update_install_progress("dashboard", 80)

                    installed_version = self._read_dashboard_version(
                        new_dashboard_config
                    )
                    success = True
                else:
                    raise AssetManagerException(
                        f"Dashboard config file not found: {dashboard_file_path}"
                    )
            else:
                raise AssetManagerException(
                    f"Unable to create dashboard {self._dashboard_key}"
                )
        else:
            _LOGGER.debug("Updating dashboard")
            if new_dashboard_config := await self.hass.async_add_executor_job(
                load_yaml_dict, dashboard_file_path
            ):
                lovelace: LovelaceData = self.hass.data["lovelace"]
                dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
                    self._dashboard_key
                )
                # Load dashboard config data
                if dashboard_store:
                    old_dashboard_config = await dashboard_store.async_load(False)

                    # Copy views to updated dashboard
                    new_dashboard_config["views"] = old_dashboard_config.get("views")

                    # Apply
                    await dashboard_store.async_save(new_dashboard_config)
                    self._update_install_progress("dashboard", 80)

                    if not discard_user_dashboard_changes:
                        await self._apply_user_dashboard_changes()

                    self._update_install_progress("dashboard", 90)

                    installed_version = self._read_dashboard_version(
                        new_dashboard_config
                    )
                    success = True
                else:
                    raise AssetManagerException("Error getting dashboard store")
            else:
                raise AssetManagerException(
                    f"Dashboard config file not found: {dashboard_file_path}"
                )

        self._update_install_progress("dashboard", 100)
        self.ignore_change_events = False
        _LOGGER.debug(
            "Dashboard successfully installed - version %s",
            installed_version,
        )
        return InstallStatus(
            installed=success,
            version=installed_version,
            latest_version=installed_version
            if downloaded and success
            else await self.async_get_latest_version(DASHBOARD_DIR),
        )

    async def async_save(self, name: str) -> bool:
        """Save asset."""
        # Dashboard automatically saves differences when changed
        return True

    @property
    def _dashboard_key(self) -> str:
        """Return path for dashboard name."""
        return DASHBOARD_NAME.replace(" ", "-").lower()

    def _read_dashboard_version(self, dashboard_config: dict[str, Any]) -> str:
        """Get view version from config."""
        if dashboard_config:
            try:
                if variables := get_key(
                    "button_card_templates.variable_template.variables",
                    dashboard_config,
                ):
                    return variables.get("dashboardversion", "0.0.0")
            except KeyError:
                _LOGGER.debug("Dashboard version not found")
        return "0.0.0"

    async def _download_dashboard(self, cancel_if_exists: bool = False) -> bool:
        """Download dashboard file."""
        # Ensure download to path exists
        base = self.hass.config.path(f"{DOMAIN}/{DASHBOARD_DIR}")

        if cancel_if_exists and Path(base, f"{DASHBOARD_DIR}.yaml").exists():
            return False

        # Validate view dir on repo
        dir_url = f"{DASHBOARD_VIEWS_GITHUB_PATH}/{DASHBOARD_DIR}"
        if await self.download_manager.async_dir_exists(dir_url):
            # Download dashboard files
            await self.download_manager.async_download_dir(dir_url, base)
        return True

    async def _dashboard_changed(self, event: Event):
        # If in dashboard build mode, ignore changes
        if self.ignore_change_events:
            return

        if event.data["url_path"] == self._dashboard_key:
            try:
                lovelace: LovelaceData = self.hass.data["lovelace"]
                dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
                    self._dashboard_key
                )
                # Load dashboard config data
                if dashboard_store:
                    dashboard_config = await dashboard_store.async_load(False)

                    # Remove views from dashboard config for saving
                    dashboard_only = dashboard_config.copy()
                    dashboard_only["views"] = [{"title": "Home"}]

                    file_path = Path(self.hass.config.config_dir, DOMAIN, DASHBOARD_DIR)
                    file_path.mkdir(parents=True, exist_ok=True)

                    if diffs := await self._compare_dashboard_to_master(dashboard_only):
                        await self.hass.async_add_executor_job(
                            save_yaml,
                            Path(file_path, "user_dashboard.yaml"),
                            diffs,
                        )

            except Exception as ex:  # noqa: BLE001
                _LOGGER.error("Error saving dashboard. Error is %s", ex)

    async def _compare_dashboard_to_master(
        self, comp_dash: dict[str, Any]
    ) -> dict[str, Any]:
        """Compare dashboard dict to master and return differences."""
        # Get master dashboard
        base = self.hass.config.path(DOMAIN)
        dashboard_file_path = f"{base}/{DASHBOARD_DIR}/{DASHBOARD_DIR}.yaml"

        if not Path(dashboard_file_path).exists():
            # No master dashboard
            return None

        # Load dashboard config file from path
        if master_dashboard := await self.hass.async_add_executor_job(
            load_yaml_dict, dashboard_file_path
        ):
            if not operator.eq(master_dashboard, comp_dash):
                diffs = dictdiff.diff(master_dashboard, comp_dash, expand=True)
                return differ_to_json(diffs)
        return None

    async def _apply_user_dashboard_changes(self):
        """Apply a user_dashboard changes file to master dashboard."""

        # Get master dashboard
        base = self.hass.config.path(DOMAIN)
        user_dashboard_file_path = f"{base}/{DASHBOARD_DIR}/user_dashboard.yaml"

        if not Path(user_dashboard_file_path).exists():
            # No master dashboard
            return

        # Load dashboard config file from path
        _LOGGER.debug("Applying user changes to dashboard")
        if user_dashboard := await self.hass.async_add_executor_job(
            load_yaml_dict, user_dashboard_file_path
        ):
            lovelace: LovelaceData = self.hass.data["lovelace"]
            dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
                self._dashboard_key
            )
            # Load dashboard config data
            if dashboard_store:
                dashboard_config = await dashboard_store.async_load(False)

                # Apply
                user_changes = json_to_dictdiffer(user_dashboard)
                updated_dashboard = dictdiff.patch(user_changes, dashboard_config)
                await dashboard_store.async_save(updated_dashboard)
