"""Blueprint manager for View Assist."""

import asyncio
import logging
from pathlib import Path
import re
from typing import Any

import voluptuous as vol

from homeassistant.components.blueprint import errors, importer, models
from homeassistant.const import ATTR_NAME
from homeassistant.helpers import config_validation as cv
from homeassistant.util.yaml import load_yaml_dict

from ..const import (  # noqa: TID252
    BLUEPRINT_GITHUB_PATH,
    COMMUNITY_BLUEPRINTS_DIR,
    COMMUNITY_VIEWS_DIR,
    CONF_ENABLED_COMMUNITY_BLUEPRINTS,
    CONF_ENABLED_COMMUNITY_VIEWS,
    CONF_ENABLED_CUSTOM_BLUEPRINTS,
    CONF_ENABLED_CUSTOM_VIEWS,
    CUSTOM_BLUEPRINTS_DIR,
    DOMAIN,
    GITHUB_BRANCH,
    GITHUB_DEV_BRANCH,
    GITHUB_REPO,
)
from ..helpers import (
    find_linked_blueprints_for_view,
    get_available_community_blueprints,
    get_available_custom_blueprints,
)
from .base import AssetManagerException, BaseAssetManager, InstallStatus

LOAD_BLUEPRINT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): cv.ensure_list,
    }
)

_LOGGER = logging.getLogger(__name__)

BLUEPRINT_MANAGER = "blueprint_manager"


class BlueprintManager(BaseAssetManager):
    """Manage blueprints for View Assist."""

    async def async_setup(self) -> None:
        """Set up the BlueprintManager."""
        self._ensure_directories()
        comm_dir = Path(
            self.hass.config.path(DOMAIN, "blueprints", COMMUNITY_BLUEPRINTS_DIR)
        )
        if not any(comm_dir.rglob("*.yaml")) and not any(comm_dir.rglob("*.yml")):
            _LOGGER.debug("Community blueprints cache is empty, downloading from repo")
            await self._download_community_blueprints()

    async def async_onboard(self, force: bool = False) -> None:
        """Load blueprints for initialisation."""
        self._ensure_directories()

        # Check if onboarding is needed and if so, run it
        if not self.data or force:
            self.onboarding = True
            bp_versions = {}

            # Cache community blueprints from repo if available
            await self._download_community_blueprints()

            # Ensure the blueprint automations domain has been loaded
            try:
                async with asyncio.timeout(30):
                    while not self.hass.data.get("blueprint", {}).get("automation"):
                        _LOGGER.debug(
                            "Blueprint automations domain not loaded yet - waiting"
                        )
                        await asyncio.sleep(1)
            except TimeoutError:
                _LOGGER.error(
                    "Timed out waiting for blueprint automations domain to load"
                )
                return None

            blueprints = await self._get_blueprint_list()
            for name in blueprints:
                try:
                    if self.is_installed(name):
                        installed_version = await self.async_get_installed_version(name)
                        latest_version = await self.async_get_latest_version(name)
                        _LOGGER.debug(
                            "Blueprint %s already installed. Registering version - %s",
                            name,
                            installed_version,
                        )
                        bp_versions[name] = {
                            "installed": installed_version,
                            "latest": latest_version,
                        }
                        continue

                    result = await self.async_install_or_update(
                        name=name, download=True
                    )
                    if result.installed:
                        bp_versions[name] = {
                            "installed": result.version,
                            "latest": result.latest_version,
                        }

                except AssetManagerException as ex:
                    _LOGGER.error("Failed to load blueprint %s: %s", name, ex)
                    continue
            self.onboarding = False
            return bp_versions
        return None

    def _ensure_directories(self) -> None:
        """Ensure blueprint cache directories exist."""
        base = Path(self.hass.config.path(DOMAIN, "blueprints"))
        base.mkdir(parents=True, exist_ok=True)
        (base / COMMUNITY_BLUEPRINTS_DIR).mkdir(parents=True, exist_ok=True)
        (base / CUSTOM_BLUEPRINTS_DIR).mkdir(parents=True, exist_ok=True)

    async def async_get_last_commit(self) -> str | None:
        """Get if the repo has a new update."""
        return await self.download_manager.get_last_commit_id(
            f"{BLUEPRINT_GITHUB_PATH}"
        )

    async def async_get_latest_version(self, name: str) -> str:
        """Get the latest version of a blueprint."""
        if bp := await self._get_blueprint_from_repo(name):
            return self._read_blueprint_version(bp.blueprint.metadata)
        return None

    async def async_get_installed_version(self, name: str) -> str | None:
        """Get the installed version of a blueprint."""
        path = Path(
            self.hass.config.path(models.BLUEPRINT_FOLDER),
            "automation",
            "dinki",
            f"blueprint-{name.replace('_', '').lower()}.yaml",
        )
        if path.exists():
            if data := await self.hass.async_add_executor_job(load_yaml_dict, path):
                blueprint = models.Blueprint(data, schema=importer.BLUEPRINT_SCHEMA)
                return self._read_blueprint_version(blueprint.metadata)
        return None

    async def async_get_version_info(
        self, update_from_repo: bool = True
    ) -> dict[str, str]:
        """Get the latest versions of blueprints."""
        bp_versions = {}
        if update_from_repo:
            await self._download_community_blueprints()

        if blueprints := await self._get_blueprint_list():
            for name in blueprints:
                installed_version = await self.async_get_installed_version(name)
                latest_version = (
                    await self.async_get_latest_version(name)
                    if update_from_repo
                    else self.data.get(name, {}).get("latest")
                )
                bp_versions[name] = {
                    "installed": installed_version,
                    "latest": latest_version,
                }
        return bp_versions

    def is_installed(self, name: str) -> bool:
        """Return if blueprint exists."""
        clean_name = name.replace(" ", "").replace("_", "").lower()
        if not clean_name.startswith("blueprint-"):
            clean_name = f"blueprint-{clean_name}"
        if not clean_name.endswith(".yaml"):
            clean_name = f"{clean_name}.yaml"

        path_dinki = Path(
            self.hass.config.path(models.BLUEPRINT_FOLDER),
            "automation",
            "dinki",
            clean_name,
        )
        path_custom = Path(
            self.hass.config.path(models.BLUEPRINT_FOLDER),
            "automation",
            "custom",
            clean_name,
        )
        return path_dinki.exists() or path_custom.exists()

    async def async_install_or_update(
        self,
        name: str,
        download: bool = False,
        dev_branch: bool = False,
        discard_user_dashboard_changes: bool = False,
        backup_existing: bool = False,
    ) -> InstallStatus:
        """Install or update blueprint from repo."""
        self._ensure_directories()
        success = False
        installed = self.is_installed(name)

        _LOGGER.debug("%s blueprint %s", "Updating" if installed else "Adding", name)

        self._update_install_progress(name, 10)

        if not download:
            raise AssetManagerException(
                "Download is required to install or update a blueprint"
            )

        if backup_existing and installed:
            _LOGGER.debug("Backing up existing blueprint %s", name)
            await self.async_save(name)

        self._update_install_progress(name, 30)

        # Install blueprint
        _LOGGER.debug("Downloading blueprint %s", name)
        bp = await self._get_blueprint_from_repo(
            name, branch=GITHUB_DEV_BRANCH if dev_branch else GITHUB_BRANCH
        )

        self._update_install_progress(name, 60)

        domain_blueprints: models.DomainBlueprints = self.hass.data["blueprint"].get(
            bp.blueprint.domain
        )
        if domain_blueprints is None:
            raise AssetManagerException(
                f"Invalid blueprint domain for {name}: {bp.blueprint.domain}"
            )

        path = bp.suggested_filename
        if not path.endswith(".yaml"):
            path = f"{path}.yaml"

        try:
            _LOGGER.debug("Installing blueprint %s", path)
            await domain_blueprints.async_add_blueprint(
                bp.blueprint, path, allow_override=True
            )
            success = True
        except errors.FileAlreadyExists as ex:
            if not self.onboarding:
                raise AssetManagerException(
                    f"Error downloading blueprint {bp.suggested_filename} - already exists."
                ) from ex
            success = self.onboarding
        except OSError as ex:
            raise AssetManagerException(
                f"Failed to download blueprint {bp.suggested_filename}: {ex}"
            ) from ex

        self._update_install_progress(name, 90)

        version = self._read_blueprint_version(bp.blueprint.metadata)
        self._update_install_progress(name, 100)
        _LOGGER.debug(
            "Blueprint %s successfully installed - version %s",
            name,
            version,
        )
        return InstallStatus(
            installed=success,
            version=version,
            latest_version=version,
        )

    async def async_install_local_blueprint(
        self, file_path: Path, target_folder: str = "dinki"
    ) -> bool:
        """Install a blueprint from a local file into Home Assistant."""
        if not file_path.exists():
            _LOGGER.error("Blueprint file does not exist: %s", file_path)
            return False

        try:
            data = await self.hass.async_add_executor_job(load_yaml_dict, str(file_path))
            if not data:
                return False

            blueprint = models.Blueprint(data, schema=importer.BLUEPRINT_SCHEMA)
            domain_blueprints: models.DomainBlueprints = self.hass.data["blueprint"].get(
                blueprint.domain
            )
            if domain_blueprints is None:
                _LOGGER.error("Invalid domain for blueprint %s", file_path.name)
                return False

            filename = file_path.name
            dest_rel_path = f"{target_folder}/{filename}"

            await domain_blueprints.async_add_blueprint(
                blueprint, dest_rel_path, allow_override=True
            )
            _LOGGER.debug("Installed local blueprint %s to %s", file_path.name, dest_rel_path)
            return True
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Failed to install local blueprint %s: %s", file_path, ex)
            return False

    async def async_uninstall_blueprint(
        self, filename_or_name: str, target_folder: str = "dinki"
    ) -> bool:
        """Uninstall/delete a blueprint from Home Assistant."""
        clean_name = filename_or_name
        if "/" in clean_name:
            clean_name = clean_name.split("/")[-1]

        clean_slug = clean_name.replace(" ", "").replace("_", "").lower()
        if not clean_slug.startswith("blueprint-"):
            clean_slug = f"blueprint-{clean_slug}"
        if not clean_slug.endswith(".yaml"):
            clean_slug = f"{clean_slug}.yaml"

        target_paths = [
            Path(self.hass.config.path(models.BLUEPRINT_FOLDER), "automation", target_folder, clean_name),
            Path(self.hass.config.path(models.BLUEPRINT_FOLDER), "automation", target_folder, clean_slug),
            Path(self.hass.config.path(models.BLUEPRINT_FOLDER), "automation", "dinki", clean_slug),
            Path(self.hass.config.path(models.BLUEPRINT_FOLDER), "automation", "custom", clean_slug),
        ]

        removed = False
        for path in target_paths:
            if path.exists():
                try:
                    path.unlink()
                    removed = True
                    _LOGGER.debug("Deleted blueprint file %s", path)
                except Exception as ex:  # noqa: BLE001
                    _LOGGER.error("Failed to delete blueprint %s: %s", path, ex)

        if filename_or_name in self.data:
            self.data.pop(filename_or_name)

        return removed

    async def async_sync_blueprints(self, options: dict[str, Any]) -> None:
        """Synchronize community and custom blueprints according to configured views and options."""
        self._ensure_directories()
        enabled_community_views = options.get(CONF_ENABLED_COMMUNITY_VIEWS, [])
        enabled_custom_views = options.get(CONF_ENABLED_CUSTOM_VIEWS, [])
        enabled_comm_bps = options.get(CONF_ENABLED_COMMUNITY_BLUEPRINTS, [])
        enabled_custom_bps = options.get(CONF_ENABLED_CUSTOM_BLUEPRINTS, [])

        _LOGGER.debug(
            "Syncing blueprints: comm_views=%s, custom_views=%s, comm_bps=%s, custom_bps=%s",
            enabled_community_views,
            enabled_custom_views,
            enabled_comm_bps,
            enabled_custom_bps,
        )

        # Collect all desired blueprints (file paths)
        target_blueprints: dict[str, Path] = {}

        # 1. Linked blueprints for enabled community views
        for view_key in enabled_community_views:
            linked_files = find_linked_blueprints_for_view(self.hass, view_key, "community")
            bp_base = Path(self.hass.config.path(DOMAIN, "blueprints", COMMUNITY_BLUEPRINTS_DIR))
            for linked in linked_files:
                target_blueprints[linked] = bp_base / linked

        # 2. Linked blueprints for enabled custom views
        for view_key in enabled_custom_views:
            linked_files = find_linked_blueprints_for_view(self.hass, view_key, "custom")
            bp_base = Path(self.hass.config.path(DOMAIN, "blueprints", CUSTOM_BLUEPRINTS_DIR))
            for linked in linked_files:
                target_blueprints[linked] = bp_base / linked

        # 3. Selected standalone community blueprints
        available_comm_bps = get_available_community_blueprints(self.hass)
        for bp_key in enabled_comm_bps:
            if bp_key in available_comm_bps:
                target_blueprints[bp_key] = Path(available_comm_bps[bp_key]["full_path"])

        # 4. Selected custom blueprints
        available_custom_bps = get_available_custom_blueprints(self.hass)
        for bp_key in enabled_custom_bps:
            if bp_key in available_custom_bps:
                target_blueprints[bp_key] = Path(available_custom_bps[bp_key]["full_path"])

        # Install target blueprints
        for bp_id, bp_file in target_blueprints.items():
            if bp_file.exists():
                await self.async_install_local_blueprint(bp_file, target_folder="dinki")

        # Reconcile unselected community/custom blueprints
        # Check all possible community and custom blueprint files
        all_possible_comm = available_comm_bps
        for bp_key, info in all_possible_comm.items():
            # If not in target blueprints and not linked to any enabled view
            if bp_key not in target_blueprints and info["file"] not in [p.name for p in target_blueprints.values()]:
                # Check if it was installed and remove
                await self.async_uninstall_blueprint(info["file"], target_folder="dinki")

    async def async_save(self, name: str) -> bool:
        """Save asset."""
        bp_file = f"blueprint-{name.replace(' ', '').replace('_', '').lower()}.yaml"
        bp_path = Path(
            self.hass.config.path(models.BLUEPRINT_FOLDER),
            "automation",
            "dinki",
            bp_file,
        )
        if bp_path.exists():
            backup_path = Path(
                self.hass.config.path(DOMAIN),
                "blueprints",
                name.replace(" ", "_"),
                bp_file.replace(".yaml", ".saved.yaml"),
            )
            await self.hass.async_add_executor_job(
                self._copy_file_to_dir, bp_path, backup_path
            )
            _LOGGER.debug("Blueprint %s saved to %s", name, backup_path)
            return True

        raise AssetManagerException(f"Error saving blueprint {name} - does not exist")

    def _copy_file_to_dir(self, source_file: Path, dest_file: Path) -> None:
        """Copy a file to a directory."""
        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            with Path.open(dest_file, "wb", encoding="utf-8") as f:
                f.write(source_file.read_bytes())
        except OSError as ex:
            raise AssetManagerException(
                f"Error copying {source_file} to {dest_file}: {ex}"
            ) from ex

    async def _get_blueprint_list(self) -> list[str]:
        """Get the list of core blueprints from repo."""
        if data := await self.download_manager.async_get_dir_listing(
            BLUEPRINT_GITHUB_PATH
        ):
            return [
                bp.name
                for bp in data
                if bp.type == "dir"
                if bp.name != COMMUNITY_VIEWS_DIR
            ]
        return []

    async def _download_community_blueprints(self) -> bool:
        """Download community blueprints from repo into local cache."""
        comm_dir = Path(
            self.hass.config.path(DOMAIN, "blueprints", COMMUNITY_BLUEPRINTS_DIR)
        )
        comm_dir.mkdir(parents=True, exist_ok=True)
        repo_path = f"{BLUEPRINT_GITHUB_PATH}/{COMMUNITY_BLUEPRINTS_DIR}"
        try:
            _LOGGER.debug("Downloading community blueprints from repo path: %s", repo_path)
            return await self.download_manager.async_download_dir(
                repo_path, str(comm_dir)
            )
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Could not download community blueprints from repo: %s", ex)
        return False

    def _read_blueprint_version(self, blueprint_config: dict[str, Any]) -> str:
        """Get view version from config."""
        if blueprint_config.get("description"):
            match = re.search(r"\bv\s?(\d+(\.\d+)+)\b", blueprint_config["description"])
            return match.group(1) if match else "0.0.0"
        return "0.0.0"

    def _get_blueprint_path(self, bp_name: str) -> str:
        """Get the URL for a blueprint."""
        return f"{BLUEPRINT_GITHUB_PATH}/{bp_name}/blueprint-{bp_name.replace('_', '').lower()}.yaml"

    async def _get_blueprint_from_repo(
        self, name: str, branch: str = GITHUB_BRANCH
    ) -> importer.ImportedBlueprint:
        """Get the blueprint from the repo."""
        try:
            path = self._get_blueprint_path(name)
            url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{branch}/{path}"
            return await importer.fetch_blueprint_from_github_url(self.hass, url)
        except Exception as ex:
            raise AssetManagerException(
                f"Error downloading blueprint {name} - {ex}"
            ) from ex
