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
    COMMUNITY_VIEWS_DIR,
    DOMAIN,
    GITHUB_BRANCH,
    GITHUB_DEV_BRANCH,
    GITHUB_REPO,
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

    async def async_onboard(self, force: bool = False) -> None:
        """Load blueprints for initialisation."""
        # Check if onboarding is needed and if so, run it
        if not self.data or force:
            # Load all blueprints
            self.onboarding = True
            bp_versions = {}

            # Ensure the blueprint automations domain has been loaded
            # issue 134
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
                        # blueprint already exists
                        installed_version = await self.async_get_installed_version(name)
                        latest_version = await self.async_get_latest_version(name)
                        _LOGGER.debug(
                            "Blueprint %s already installed.  Registering version - %s",
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
                        # Set installed version to latest if BP already existed
                        # and therefore we did not overwrite.
                        # This is to support update notifications for users migrating
                        # from prior versions of this integration.

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
        # Get the latest versions of blueprints
        bp_versions = {}
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
        """Return blueprint exists."""
        path = Path(
            self.hass.config.path(models.BLUEPRINT_FOLDER),
            "automation",
            "dinki",
            f"blueprint-{name.replace('_', '').lower()}.yaml",
        )
        return path.exists()

    async def async_install_or_update(
        self,
        name,
        download: bool = False,
        dev_branch: bool = False,
        discard_user_dashboard_changes: bool = False,
        backup_existing: bool = False,
    ) -> InstallStatus:
        """Install or update blueprint."""
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
                    f"Error downloading blueprint {bp.suggested_filename} - already exists. Use overwrite=True to overwrite"
                ) from ex
            success = self.onboarding
        except OSError as ex:
            raise AssetManagerException(
                f"Failed to download blueprint {bp.suggested_filename}: {ex}"
            ) from ex

        self._update_install_progress(name, 90)

        # Return install status
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

    async def async_save(self, name: str) -> bool:
        """Save asset."""
        # Save blueprint to file in config/view_assist/blueprints
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
            with Path.open(dest_file, "wb") as f:
                f.write(source_file.read_bytes())
        except OSError as ex:
            raise AssetManagerException(
                f"Error copying {source_file} to {dest_file}: {ex}"
            ) from ex

    async def _get_blueprint_list(self) -> list[str]:
        """Get the list of blueprints from repo."""
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
