"""Base Asset Manager class."""

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from ..const import VA_ASSET_UPDATE_PROGRESS  # noqa: TID252
from ..typed import VAConfigEntry  # noqa: TID252
from .download_manager import DownloadManager


class AssetManagerException(Exception):
    """A asset manager exception."""


@dataclass
class InstallStatus:
    """Installed status."""

    installed: bool = False
    version: str | None = None
    latest_version: str | None = None


class BaseAssetManager:
    """Base class for asset managers."""

    def __init__(
        self, hass: HomeAssistant, config: VAConfigEntry, data: dict[str, Any]
    ) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.data = data
        self.download_manager = DownloadManager(hass)
        self.onboarding: bool = False

    async def async_setup(self) -> None:
        """Set up the AssetManager."""

    async def async_onboard(self, force: bool = False) -> dict[str, Any]:
        """Onboard the asset manager."""
        return {}

    async def async_get_installed_version(self, name: str) -> str | None:
        """Get installed version of asset."""
        if self.data and id in self.data:
            return self.data[id]["installed"]
        return None

    async def async_get_last_commit(self) -> str | None:
        """Get if the repo has a new update."""
        raise NotImplementedError

    async def async_get_latest_version(self, name: str) -> dict[str, Any]:
        """Get latest version of asset from repo."""
        raise NotImplementedError

    async def async_get_version_info(self) -> dict[str, Any]:
        """Update versions from repo."""
        raise NotImplementedError

    def is_installed(self, name: str) -> bool:
        """Return if asset is installed."""
        if self.data and name in self.data:
            return self.data[name]["installed"] is not None
        return False

    async def async_install_or_update(
        self,
        name: str,
        download: bool = False,
        dev_branch: bool = False,
        discard_user_dashboard_changes: bool = False,
        backup_existing: bool = False,
    ) -> InstallStatus:
        """Install or update asset."""
        raise NotImplementedError

    async def async_save(self, name: str) -> bool:
        """Save asset."""
        raise NotImplementedError

    def _update_install_progress(self, name: str, progress: int):
        """Update progress of view download."""
        async_dispatcher_send(
            self.hass,
            VA_ASSET_UPDATE_PROGRESS,
            {"name": name, "progress": progress},
        )
