"""HACS plugin management for View Assist."""

from dataclasses import dataclass
import logging
from typing import Any

from awesomeversion import AwesomeVersion

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from ..typed import VAConfigEntry  # noqa: TID252

HACS_DOMAIN = "hacs"
MARKETPLACE_DOMAIN = "marketplace"
_LOGGER = logging.getLogger(__name__)


@dataclass
class Plugin:
    """Represents a HACS plugin for View Assist."""

    name: str
    respository_id: str
    version: str


REQUIRED_PLUGINS = [
    Plugin(
        name="card-mod",
        respository_id="190927524",
        version="v4.2.1",
    ),
    Plugin(
        name="button-card",
        respository_id="146194325",
        version="v7.0.1",
    ),
    Plugin(
        name="layout-card",
        respository_id="156434866",
        version="v2.4.7",
    ),
]


class PluginManager:
    """Manages HACS plugins for View Assist."""

    def __init__(
        self, hass: HomeAssistant, config: VAConfigEntry, data: dict[str, Any]
    ) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.data = data
        self.onboarding: bool = False
        self.use_marketplace = False

        # If HA version is 2026.11 or greater, use marketplace not HACS for plugin management
        if AwesomeVersion(HA_VERSION) >= AwesomeVersion("2026.11.0"):
            _LOGGER.info("Using marketplace for plugin management instead of HACS")
            self.use_marketplace = True
        else:
            _LOGGER.info("Using HACS for plugin management")

    async def async_onboard(self, force: bool = False) -> dict[str, Any]:
        """Onboard the asset manager."""
        return {}

    async def install_listed_plugins(self, force: bool = False) -> dict[str, Any]:
        plugin_versions: dict[str, Any] = {}
        if not self.data or force:
            self.onboarding = True
            if self.get_repository_manager():
                for plugin in REQUIRED_PLUGINS:
                    installed, version = await self.async_get_plugin_status(
                        plugin.respository_id
                    )

                    if installed:
                        _LOGGER.info(
                            "HACS plugin %s is already installed with version %s",
                            plugin.name,
                            version,
                        )
                        plugin_versions[plugin.name] = {
                            "installed": AwesomeVersion(version)
                        }

                    elif not installed or (
                        version
                        and AwesomeVersion(version) < AwesomeVersion(plugin.version)
                    ):
                        success = await self.async_install_plugin(
                            plugin.respository_id, plugin.version
                        )
                        if success:
                            plugin_versions[plugin.name] = {
                                "installed": AwesomeVersion(plugin.version),
                            }

        return plugin_versions

    def get_repository_manager(self) -> Any | None:
        """Return the appropriate repository manager based on the current configuration."""
        if self.use_marketplace:
            marketplace = self.hass.data.get(MARKETPLACE_DOMAIN)
            if marketplace is None:
                _LOGGER.error("Marketplace is not loaded")
            return marketplace
        hacs = self.hass.data.get(HACS_DOMAIN)
        if hacs is None:
            _LOGGER.error("HACS is not installed or not loaded")
        return hacs

    def _get_repository(self, repository_id: str) -> Any | None:
        """Return the repository object for a repository id."""
        repository_manager = self.get_repository_manager()
        if repository_manager is None:
            return None

        _LOGGER.info(
            "Fetching repository %s using repository manager: %s",
            repository_id,
            repository_manager,
        )
        repository = repository_manager.repositories.get_by_id(repository_id)
        if repository is None:
            _LOGGER.error("Repository %s not found", repository_id)
        return repository

    async def async_get_plugin_status(
        self, repository_id: str
    ) -> tuple[bool, str | None]:
        """Check if a plugin is installed and return its installed version."""
        repository = self._get_repository(repository_id)
        if repository is None:
            return False, None

        if not repository.updated_info:
            try:
                await repository.update_repository(ignore_issues=True, force=True)
            except Exception as ex:  # noqa: BLE001
                _LOGGER.error(
                    "Error refreshing HACS repository %s: %s", repository_id, ex
                )
            repository.updated_info = True

        return bool(repository.data.installed), repository.display_installed_version

    async def async_install_plugin(
        self, repository_id: str, version: str | None = None
    ) -> bool:
        """Install a plugin by repository id, optionally at a specific version."""
        repository = self._get_repository(repository_id)
        if repository is None:
            return False

        try:
            await repository.async_download_repository(ref=version)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error installing plugin %s: %s", repository_id, ex)
            return False

        await self.get_repository_manager().data.async_write()
        return True

    async def async_uninstall_plugin(self, repository_id: str) -> bool:
        """Uninstall an already installed plugin."""
        repository = self._get_repository(repository_id)
        if repository is None:
            return False

        try:
            await repository.uninstall()
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error uninstalling plugin %s: %s", repository_id, ex)
            return False

        await self.get_repository_manager().data.async_write()
        return True

    def check_for_plugin(self, plugin_id: str) -> bool:
        """Check if a plugin id is in the tracked plugins list."""
        return any(plugin.repository_id == plugin_id for plugin in self.plugins)

    def get_plugins(self):
        """Return the tracked plugins list."""
        return self.plugins
