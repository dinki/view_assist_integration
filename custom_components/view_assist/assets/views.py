"""Assets manager for views."""

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.lovelace import LovelaceData, dashboard
from homeassistant.const import EVENT_PANELS_UPDATED
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.yaml import load_yaml_dict, parse_yaml, save_yaml

from ..const import (  # noqa: TID252
    COMMUNITY_VIEWS_DIR,
    DASHBOARD_NAME,
    DASHBOARD_VIEWS_GITHUB_PATH,
    DEFAULT_VIEW,
    DOMAIN,
    GITHUB_BRANCH,
    GITHUB_DEV_BRANCH,
    VIEWS_DIR,
)
from .base import AssetManagerException, BaseAssetManager, InstallStatus

_LOGGER = logging.getLogger(__name__)


class ViewManager(BaseAssetManager):
    """Class to manage view assets."""

    async def async_onboard(self, force: bool = False) -> dict[str, Any] | None:
        """Onboard the user if not yet setup."""
        # Check if onboarding is needed and if so, run it
        if not self.data or force:
            self.onboarding = True
            vw_versions = {}
            views = await self._async_get_view_list()
            for view in views:
                # If dashboard and views exist and we are just migrating to managed views
                if await self.async_is_installed(view):
                    # Download latest version of view
                    await self._download_view(view, cancel_if_exists=True)

                    installed_version = await self.async_get_installed_version(view)
                    latest_version = await self.async_get_latest_version(view)
                    _LOGGER.debug(
                        "View %s already installed.  Registering version - %s",
                        view,
                        installed_version,
                    )
                    vw_versions[view] = {
                        "installed": installed_version,
                        "latest": latest_version,
                    }
                    continue

                # Install view from already downloaded file or repo
                result = await self.async_install_or_update(view, download=True)
                if result.installed:
                    vw_versions[view] = {
                        "installed": result.version,
                        "latest": result.latest_version,
                    }

            # Delete Home view from default dashboard
            await self.delete_view("home")

            self.onboarding = False
            return vw_versions
        return None

    async def async_get_last_commit(self) -> str | None:
        """Get if the repo has a new update."""
        return await self.download_manager.get_last_commit_id(
            f"{DASHBOARD_VIEWS_GITHUB_PATH}/{VIEWS_DIR}"
        )

    async def async_get_installed_version(self, name: str) -> str | None:
        """Get installed version of asset."""
        if view_config := await self._async_get_view_config(name):
            # Get installed version from config
            return self._read_view_version(name, view_config)
        return None

    async def async_get_latest_version(self, name: str) -> str | None:
        """Get latest version of asset."""
        view_path = f"{DASHBOARD_VIEWS_GITHUB_PATH}/{VIEWS_DIR}/{name}/{name}.yaml"
        if view_data := await self.download_manager.get_file_contents(view_path):
            # Parse yaml string to json
            try:
                view_data = parse_yaml(view_data)
                return self._read_view_version(name, view_data)
            except HomeAssistantError:
                _LOGGER.error("Failed to parse view %s", name)
        return None

    async def async_get_version_info(
        self, update_from_repo: bool = True
    ) -> dict[str, Any]:
        """Update versions from repo."""
        # Get the latest versions of blueprints
        vw_versions = {}
        if blueprints := await self._async_get_view_list():
            for name in blueprints:
                installed_version = await self.async_get_installed_version(name)
                latest_version = (
                    await self.async_get_latest_version(name)
                    if update_from_repo
                    else self.data.get(name, {}).get("latest")
                )
                vw_versions[name] = {
                    "installed": installed_version,
                    "latest": latest_version,
                }
        return vw_versions

    async def async_is_installed(self, name):
        """Return if asset is installed."""
        return await self._async_get_view_index(name) > 0

    async def async_install_or_update(
        self,
        name: str,
        download: bool = False,
        dev_branch: bool = False,
        discard_user_dashboard_changes: bool = False,
        backup_existing: bool = False,
    ) -> InstallStatus:
        """Install or update asset."""

        self._update_install_progress(name, 0)
        success = False
        installed_version = None

        view_index = await self._async_get_view_index(name)
        file_path = Path(self.hass.config.path(DOMAIN), VIEWS_DIR, name)

        _LOGGER.debug("%s view %s", "Updating" if view_index else "Adding", name)

        self._update_install_progress(name, 10)

        if view_index > 0 and backup_existing:
            # Backup existing view
            _LOGGER.debug("Backing up existing view %s", name)
            await self.async_save(name)

        self._update_install_progress(name, 30)

        # Download view if required
        downloaded = False
        # Don't download if file exists during onboarding
        if self.onboarding and Path(file_path, f"{name}.yaml").exists():
            _LOGGER.debug("View file already exists for %s.  Not downloading", name)
            downloaded = True
        elif download:
            # Download view files from github repo
            _LOGGER.debug("Downloading view %s", name)
            # Set branch to download from
            if dev_branch:
                self.download_manager.set_branch(GITHUB_DEV_BRANCH)
            else:
                self.download_manager.set_branch(GITHUB_BRANCH)

            downloaded = await self._download_view(name)
            if not downloaded:
                raise AssetManagerException(
                    f"Unable to download view {name}.  Please check the view name and try again."
                )

        self._update_install_progress(name, 50)

        # Install view
        try:
            _LOGGER.debug("Installing view %s", name)
            # Load in order of existence - user view version (for later feature), default version, saved version
            file: Path = None
            file_options = [f"user_{name}.yaml", f"{name}.yaml", f"{name}.saved.yaml"]

            for file_option in file_options:
                if Path(file_path, file_option).exists():
                    file = Path(file_path, file_option)
                    break

            if file:
                new_view_config = await self.hass.async_add_executor_job(
                    load_yaml_dict, file
                )
            else:
                raise AssetManagerException(
                    f"Unable to install view {name}.  Unable to find a yaml file"
                )
        except OSError as ex:
            raise AssetManagerException(
                f"Unable to install view {name}.  Error is {ex}"
            ) from ex

        self._update_install_progress(name, 60)

        # Get lovelace (frontend) config data
        lovelace: LovelaceData = self.hass.data["lovelace"]
        # Get access to dashboard store
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )

        # Load dashboard config data
        if new_view_config and dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)

            # Create new view and add it to dashboard
            new_view = {
                "type": "panel",
                "title": name.title(),
                "path": name,
                "cards": [new_view_config],
            }

            if not dashboard_config["views"]:
                dashboard_config["views"] = [new_view]
            elif view_index:
                dashboard_config["views"][view_index - 1] = new_view
            elif name == DEFAULT_VIEW:
                # Insert default view as first view in list
                dashboard_config["views"].insert(0, new_view)
            else:
                dashboard_config["views"].append(new_view)

            self._update_install_progress(name, 90)

            # Save dashboard config back to HA
            await dashboard_store.async_save(dashboard_config)
            self.hass.bus.async_fire(EVENT_PANELS_UPDATED)

            success = True

            # Update installed version info
            installed_version = self._read_view_version(name, new_view_config)
            self._update_install_progress(name, 100)

        _LOGGER.debug(
            "View %s successfully installed - version %s",
            name,
            installed_version,
        )
        return InstallStatus(
            installed=success,
            version=installed_version,
            latest_version=installed_version
            if downloaded and success
            else await self.async_get_latest_version(name),
        )

    async def async_save(self, name: str) -> bool:
        """Backup a view to a file."""

        # Get lovelace (frontend) config data
        lovelace: LovelaceData = self.hass.data["lovelace"]

        # Get access to dashboard store
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )

        # Load dashboard config data
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)

            # Make list of existing view names for this dashboard
            for view in dashboard_config["views"]:
                if view.get("path") == name.lower():
                    file_path = Path(
                        self.hass.config.path(DOMAIN), VIEWS_DIR, name.lower()
                    )
                    file_name = f"{name.lower()}.saved.yaml"

                    if view.get("cards", []):
                        # Ensure path exists
                        file_path.mkdir(parents=True, exist_ok=True)
                        return await self.hass.async_add_executor_job(
                            save_yaml,
                            Path(file_path, file_name),
                            view.get("cards", [])[0],
                        )

                    raise AssetManagerException(f"No view data to save for {name} view")
        return False

    async def _async_get_view_list(self) -> list[str]:
        """Get the list of views from repo."""
        if data := await self.download_manager.async_get_dir_listing(
            f"{DASHBOARD_VIEWS_GITHUB_PATH}/{VIEWS_DIR}"
        ):
            return [
                view.name
                for view in data
                if view.type == "dir"
                if view.name != COMMUNITY_VIEWS_DIR
            ]
        return []

    @property
    def _dashboard_key(self) -> str:
        """Return path for dashboard name."""
        return DASHBOARD_NAME.replace(" ", "-").lower()

    @property
    def _dashboard_exists(self) -> bool:
        """Return if dashboard exists."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        return self._dashboard_key in lovelace.dashboards

    @property
    def _installed_views(self) -> list[str]:
        """Return installed views."""
        return self.data.keys()

    def _read_view_version(self, view: str, view_config: dict[str, Any]) -> str:
        """Get view version from config."""
        if view_config:
            try:
                if variables := view_config.get("variables"):
                    return variables.get(
                        f"{view}version", variables.get(f"{view}cardversion", "0.0.0")
                    )
            except KeyError:
                _LOGGER.debug("View %s version not found", view)
        return "0.0.0"

    async def _async_get_view_index(self, view: str) -> int:
        """Return index of view if view exists."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )
        # Load dashboard config data
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)
            if not dashboard_config["views"]:
                return 0

            for index, ex_view in enumerate(dashboard_config["views"]):
                if ex_view.get("path") == view:
                    return index + 1
        return 0

    async def _async_get_view_config(self, view: str) -> dict[str, Any]:
        """Get view config."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )
        # Load dashboard config data
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)
            for ex_view in dashboard_config["views"]:
                if ex_view.get("path") == view:
                    if cards := ex_view.get("cards", []):
                        if isinstance(cards, list):
                            # Get first card in list
                            return cards[0]
        return {}

    async def _download_view(
        self,
        view_name: str,
        community_view: bool = False,
        cancel_if_exists: bool = False,
    ):
        """Download view files from a github repo directory."""

        # Ensure download to path exists
        base = self.hass.config.path(f"{DOMAIN}/{VIEWS_DIR}")
        if community_view:
            dir_url = f"{DASHBOARD_VIEWS_GITHUB_PATH}/{VIEWS_DIR}/{COMMUNITY_VIEWS_DIR}/{view_name}"
        else:
            dir_url = f"{DASHBOARD_VIEWS_GITHUB_PATH}/{VIEWS_DIR}/{view_name}"

        if cancel_if_exists and Path(base, view_name, f"{view_name}.yaml").exists():
            return False

        # Validate view dir on repo
        if await self.download_manager.async_dir_exists(dir_url):
            # Create view directory
            Path(base, view_name).mkdir(parents=True, exist_ok=True)

            # Download view files
            success = await self.download_manager.async_download_dir(
                dir_url, Path(base, view_name)
            )

            # Validate yaml file and install view
            if success and Path(base, view_name, f"{view_name}.yaml").exists():
                _LOGGER.debug("Downloaded %s", view_name)
                return True

        _LOGGER.error("Failed to download %s", view_name)
        return False

    async def delete_view(self, view: str):
        """Delete view."""

        # Get lovelace (frontend) config data
        lovelace: LovelaceData = self.hass.data["lovelace"]

        # Get access to dashboard store
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )

        # Load dashboard config data
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(True)

            # Remove view with title of home
            modified = False
            for index, ex_view in enumerate(dashboard_config["views"]):
                if ex_view.get("title", "").lower() == view.lower():
                    dashboard_config["views"].pop(index)
                    modified = True
                    break

            # Save dashboard config back to HA
            if modified:
                await dashboard_store.async_save(dashboard_config)
