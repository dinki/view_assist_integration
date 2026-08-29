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
    CONF_ENABLED_COMMUNITY_VIEWS,
    CONF_ENABLED_CORE_VIEWS,
    CONF_ENABLED_CUSTOM_VIEWS,
    CONF_VIEW_VARIANTS,
    CORE_VIEWS,
    CUSTOM_VIEWS_DIR,
    DASHBOARD_NAME,
    DASHBOARD_VIEWS_GITHUB_PATH,
    DEFAULT_ENABLED_CORE_VIEWS,
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

    async def async_setup(self) -> None:
        """Set up the ViewManager."""
        self._ensure_directories()
        comm_dir = Path(self.hass.config.path(DOMAIN, VIEWS_DIR, COMMUNITY_VIEWS_DIR))
        # If community views directory is empty or missing yaml files, download from repo
        if not any(comm_dir.glob("*.yaml")) and not any(comm_dir.glob("*.yml")):
            _LOGGER.debug("Community views cache is empty, downloading from repo")
            await self._download_community_views()

    async def async_onboard(self, force: bool = False) -> dict[str, Any] | None:
        """Onboard the user if not yet setup."""
        # Ensure local directories exist
        self._ensure_directories()

        # Check if onboarding is needed and if so, run it
        if not self.data or force:
            self.onboarding = True
            vw_versions = {}

            # Cache community views from repo if available
            await self._download_community_views()

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

    def _ensure_directories(self) -> None:
        """Ensure view directories exist."""
        base = Path(self.hass.config.path(DOMAIN, VIEWS_DIR))
        base.mkdir(parents=True, exist_ok=True)
        (base / COMMUNITY_VIEWS_DIR).mkdir(parents=True, exist_ok=True)
        (base / CUSTOM_VIEWS_DIR).mkdir(parents=True, exist_ok=True)

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
        vw_versions = {}
        if update_from_repo:
            await self._download_community_views()

        if views := await self._async_get_view_list():
            for name in views:
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

    async def async_is_installed(self, name: str) -> bool:
        """Return if asset is installed."""
        return await self._async_get_view_index(name) > 0

    async def async_install_or_update(
        self,
        name: str,
        variant: str | None = None,
        view_source: str = "core",
        view_path: str | None = None,
        view_title: str | None = None,
        download: bool = False,
        dev_branch: bool = False,
        discard_user_dashboard_changes: bool = False,
        backup_existing: bool = False,
    ) -> InstallStatus:
        """Install or update asset."""
        self._ensure_directories()
        self._update_install_progress(name, 0)
        success = False
        installed_version = None

        target_path = (
            view_path
            or CORE_VIEWS.get(name, {}).get("path")
            or name.lower().replace(" ", "_")
        )
        target_title = (
            view_title
            or CORE_VIEWS.get(name, {}).get("title")
            or name.replace("_", " ").title()
        )

        view_index = await self._async_get_view_index(target_path)
        base_views_dir = Path(self.hass.config.path(DOMAIN, VIEWS_DIR))

        _LOGGER.debug(
            "%s view %s (variant: %s, source: %s, path: %s)",
            "Updating" if view_index else "Adding",
            name,
            variant,
            view_source,
            target_path,
        )

        self._update_install_progress(name, 10)

        if view_index > 0 and backup_existing:
            _LOGGER.debug("Backing up existing view %s", target_path)
            await self.async_save(target_path)

        self._update_install_progress(name, 30)

        # Download view if required
        downloaded = False
        if download:
            if dev_branch:
                self.download_manager.set_branch(GITHUB_DEV_BRANCH)
            else:
                self.download_manager.set_branch(GITHUB_BRANCH)

            if view_source == "core":
                file_path = base_views_dir / name
                if self.onboarding and (file_path / f"{name}.yaml").exists():
                    _LOGGER.debug("View file already exists for %s. Not downloading", name)
                    downloaded = True
                else:
                    _LOGGER.debug("Downloading view %s", name)
                    downloaded = await self._download_view(name)
            elif view_source == "community":
                _LOGGER.debug("Downloading community views from repo")
                downloaded = await self._download_community_views()

        self._update_install_progress(name, 50)

        # Find and load the view YAML file
        try:
            file_to_load = self._resolve_view_file(
                name=name,
                variant=variant,
                view_source=view_source,
            )

            # If file does not exist locally and is from community contributions, try downloading
            if (not file_to_load or not file_to_load.exists()) and (
                view_source == "community"
                or (
                    variant
                    and "community"
                    in str(
                        CORE_VIEWS.get(name, {})
                        .get("variants", {})
                        .get(variant, {})
                        .get("file", "")
                    )
                )
            ):
                _LOGGER.debug(
                    "View file not found in local cache for %s (%s), attempting download",
                    name,
                    variant,
                )
                await self._download_community_views()
                file_to_load = self._resolve_view_file(
                    name=name,
                    variant=variant,
                    view_source=view_source,
                )

            if not file_to_load or not file_to_load.exists():
                raise AssetManagerException(
                    f"Unable to install view {name}. File not found: {file_to_load}"
                )

            new_view_config = await self.hass.async_add_executor_job(
                load_yaml_dict, file_to_load
            )
            if not new_view_config or not isinstance(new_view_config, dict):
                raise AssetManagerException(
                    f"Unable to install view {name}. File is empty or invalid YAML: {file_to_load}"
                )
        except OSError as ex:
            raise AssetManagerException(
                f"Unable to install view {name}. Error: {ex}"
            ) from ex

        self._update_install_progress(name, 60)

        # Get lovelace dashboard store
        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )

        if new_view_config and dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)

            # Check if root YAML is already a full view configuration
            if isinstance(new_view_config, dict) and "cards" in new_view_config:
                new_view = {
                    "type": new_view_config.get("type", "panel"),
                    "title": new_view_config.get("title", target_title),
                    "path": new_view_config.get("path", target_path),
                    "cards": new_view_config.get("cards", []),
                }
                if "badges" in new_view_config:
                    new_view["badges"] = new_view_config["badges"]
            else:
                # Wrap card config as a panel view
                new_view = {
                    "type": "panel",
                    "title": target_title,
                    "path": target_path,
                    "cards": [new_view_config],
                }

            if not dashboard_config.get("views"):
                dashboard_config["views"] = [new_view]
            elif view_index > 0:
                dashboard_config["views"][view_index - 1] = new_view
            elif target_path == DEFAULT_VIEW:
                dashboard_config["views"].insert(0, new_view)
            else:
                dashboard_config["views"].append(new_view)

            self._update_install_progress(name, 90)

            # Save dashboard config back to HA
            await dashboard_store.async_save(dashboard_config)
            self.hass.bus.async_fire(EVENT_PANELS_UPDATED)

            success = True
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

    def _resolve_view_file(
        self,
        name: str,
        variant: str | None = None,
        view_source: str = "core",
    ) -> Path | None:
        """Resolve the path to the view YAML file."""
        base_views_dir = Path(self.hass.config.path(DOMAIN, VIEWS_DIR))

        if view_source == "community":
            comm_dir = base_views_dir / COMMUNITY_VIEWS_DIR
            options = [
                comm_dir / f"{name}.yaml",
                comm_dir / f"{name}.yml",
                comm_dir / name / f"{name}.yaml",
            ]
            for opt in options:
                if opt.exists():
                    return opt
            return options[0]

        if view_source == "custom":
            cust_dir = base_views_dir / CUSTOM_VIEWS_DIR
            options = [
                cust_dir / f"{name}.yaml",
                cust_dir / f"{name}.yml",
                cust_dir / name / f"{name}.yaml",
            ]
            for opt in options:
                if opt.exists():
                    return opt
            return options[0]

        # Core views
        file_path = base_views_dir / name
        core_info = CORE_VIEWS.get(name, {})

        if variant:
            if variant in core_info.get("variants", {}):
                var_file = core_info["variants"][variant]["file"]
                if "/" in var_file:
                    target = base_views_dir / var_file
                    if target.exists():
                        return target
                else:
                    target = file_path / var_file
                    if target.exists():
                        return target

            # Check direct variant file in core folder
            if (file_path / f"{variant}.yaml").exists():
                return file_path / f"{variant}.yaml"
            if (file_path / f"{variant}.yml").exists():
                return file_path / f"{variant}.yml"

            # Backward compatibility for community / custom variant files
            if (base_views_dir / COMMUNITY_VIEWS_DIR / f"{variant}.yaml").exists():
                return base_views_dir / COMMUNITY_VIEWS_DIR / f"{variant}.yaml"
            if (base_views_dir / CUSTOM_VIEWS_DIR / f"{variant}.yaml").exists():
                return base_views_dir / CUSTOM_VIEWS_DIR / f"{variant}.yaml"

        # Default search order for core views
        file_options = [
            file_path / f"user_{name}.yaml",
            file_path / f"{name}.yaml",
            file_path / f"{name}.saved.yaml",
        ]
        # Check if single YAML in views dir exists (e.g. clockalt.yaml)
        if (file_path / f"{name}.yaml").exists():
            return file_path / f"{name}.yaml"

        for opt in file_options:
            if opt.exists():
                return opt

        # Fallback for standalone core variant files like clockalt
        if (base_views_dir / "clock" / f"{name}.yaml").exists():
            return base_views_dir / "clock" / f"{name}.yaml"

        return file_path / f"{name}.yaml"

    async def async_uninstall_view(
        self, name: str, view_path: str | None = None
    ) -> bool:
        """Uninstall a view from the dashboard."""
        target_path = (
            view_path
            or CORE_VIEWS.get(name, {}).get("path")
            or name.lower().replace(" ", "_")
        )

        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )

        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)
            if not dashboard_config.get("views"):
                return False

            removed = False
            for index, ex_view in enumerate(list(dashboard_config["views"])):
                if (
                    ex_view.get("path") == target_path
                    or ex_view.get("title", "").lower() == name.lower()
                ):
                    dashboard_config["views"].pop(index)
                    removed = True
                    _LOGGER.debug("Removed view %s from dashboard", target_path)
                    break

            if removed:
                await dashboard_store.async_save(dashboard_config)
                self.hass.bus.async_fire(EVENT_PANELS_UPDATED)
                if name in self.data:
                    self.data.pop(name)
                return True

        return False

    async def async_sync_configured_views(self, options: dict[str, Any]) -> None:
        """Synchronize dashboard views according to configuration options."""
        enabled_core = options.get(CONF_ENABLED_CORE_VIEWS, DEFAULT_ENABLED_CORE_VIEWS)
        variants = options.get(CONF_VIEW_VARIANTS, {})
        enabled_community = options.get(CONF_ENABLED_COMMUNITY_VIEWS, [])
        enabled_custom = options.get(CONF_ENABLED_CUSTOM_VIEWS, [])

        _LOGGER.debug(
            "Syncing views: core=%s, variants=%s, community=%s, custom=%s",
            enabled_core,
            variants,
            enabled_community,
            enabled_custom,
        )

        # Build target view definitions
        target_views: dict[str, dict[str, Any]] = {}

        # 1. Enabled core views
        for core_name in enabled_core:
            if core_name in CORE_VIEWS:
                variant = variants.get(core_name)
                target_views[core_name] = {
                    "name": core_name,
                    "variant": variant,
                    "source": "core",
                    "path": CORE_VIEWS[core_name]["path"],
                    "title": CORE_VIEWS[core_name]["title"],
                }

        # 2. Community views
        for comm_name in enabled_community:
            target_views[f"comm_{comm_name}"] = {
                "name": comm_name,
                "variant": None,
                "source": "community",
                "path": comm_name,
                "title": comm_name.replace("_", " ").title(),
            }

        # 4. Custom user views
        for cust_name in enabled_custom:
            target_views[f"cust_{cust_name}"] = {
                "name": cust_name,
                "variant": None,
                "source": "custom",
                "path": cust_name,
                "title": f"{cust_name.replace('_', ' ').title()}",
            }

        # Load current dashboard views
        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )
        if not dashboard_store:
            return

        dashboard_config = await dashboard_store.async_load(False)
        existing_paths = {
            v.get("path"): v for v in dashboard_config.get("views", []) if v.get("path")
        }

        # Uninstall views that are managed but not in target list
        # We only remove paths that belong to known core views or clockalt or community/custom
        known_manageable_paths = set()
        for k, v in CORE_VIEWS.items():
            known_manageable_paths.add(v["path"])
        known_manageable_paths.add("clockalt")

        target_paths = {info["path"] for info in target_views.values()}

        # Remove disabled core views
        for path in list(existing_paths.keys()):
            if (
                path in known_manageable_paths or path.startswith("cust_")
            ) and path not in target_paths:
                await self.async_uninstall_view(path, view_path=path)

        # Also remove any unselected community views
        # Check all local community views
        comm_dir = Path(
            self.hass.config.path(DOMAIN, VIEWS_DIR, COMMUNITY_VIEWS_DIR)
        )
        if comm_dir.exists():
            for comm_file in comm_dir.glob("*.yaml"):
                comm_key = comm_file.stem
                if comm_key in existing_paths and comm_key not in target_paths:
                    await self.async_uninstall_view(comm_key, view_path=comm_key)

        # Install or update target views
        for target_info in target_views.values():
            try:
                await self.async_install_or_update(
                    name=target_info["name"],
                    variant=target_info.get("variant"),
                    view_source=target_info.get("source", "core"),
                    view_path=target_info.get("path"),
                    view_title=target_info.get("title"),
                    download=False,
                )
            except Exception as ex:  # noqa: BLE001
                _LOGGER.error("Failed to install/sync view %s: %s", target_info["name"], ex)

    async def async_save(self, name: str) -> bool:
        """Backup a view to a file."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )

        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)
            for view in dashboard_config.get("views", []):
                if view.get("path") == name.lower():
                    file_path = Path(
                        self.hass.config.path(DOMAIN), VIEWS_DIR, name.lower()
                    )
                    file_name = f"{name.lower()}.saved.yaml"

                    if view.get("cards", []):
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

    async def _download_community_views(self) -> bool:
        """Download community views from repo into local cache."""
        comm_dir = Path(
            self.hass.config.path(DOMAIN, VIEWS_DIR, COMMUNITY_VIEWS_DIR)
        )
        comm_dir.mkdir(parents=True, exist_ok=True)
        repo_path = f"{DASHBOARD_VIEWS_GITHUB_PATH}/{VIEWS_DIR}/{COMMUNITY_VIEWS_DIR}"
        try:
            _LOGGER.debug("Downloading community views from repo path: %s", repo_path)
            return await self.download_manager.async_download_dir(
                repo_path, str(comm_dir)
            )
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Could not download community views from repo: %s", ex)
        return False

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
        return list(self.data.keys())

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
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)
            if not dashboard_config.get("views"):
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
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)
            for ex_view in dashboard_config.get("views", []):
                if ex_view.get("path") == view:
                    if cards := ex_view.get("cards", []):
                        if isinstance(cards, list):
                            return cards[0]
        return {}

    async def _download_view(
        self,
        view_name: str,
        community_view: bool = False,
        cancel_if_exists: bool = False,
    ):
        """Download view files from a github repo directory."""
        base = self.hass.config.path(f"{DOMAIN}/{VIEWS_DIR}")
        if community_view:
            dir_url = f"{DASHBOARD_VIEWS_GITHUB_PATH}/{VIEWS_DIR}/{COMMUNITY_VIEWS_DIR}/{view_name}"
        else:
            dir_url = f"{DASHBOARD_VIEWS_GITHUB_PATH}/{VIEWS_DIR}/{view_name}"

        if cancel_if_exists and Path(base, view_name, f"{view_name}.yaml").exists():
            return False

        if await self.download_manager.async_dir_exists(dir_url):
            Path(base, view_name).mkdir(parents=True, exist_ok=True)
            success = await self.download_manager.async_download_dir(
                dir_url, Path(base, view_name)
            )
            if success and Path(base, view_name, f"{view_name}.yaml").exists():
                _LOGGER.debug("Downloaded %s", view_name)
                return True

        _LOGGER.error("Failed to download %s", view_name)
        return False

    async def delete_view(self, view: str):
        """Delete view by title."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self._dashboard_key
        )
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(True)
            modified = False
            for index, ex_view in enumerate(dashboard_config.get("views", [])):
                if ex_view.get("title", "").lower() == view.lower():
                    dashboard_config["views"].pop(index)
                    modified = True
                    break

            if modified:
                await dashboard_store.async_save(dashboard_config)
