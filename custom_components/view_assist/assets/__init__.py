"""Assets manager for VA."""

import asyncio
from enum import StrEnum
import logging
from typing import Any

from awesomeversion import AwesomeVersion
import voluptuous as vol

from homeassistant.const import ATTR_NAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, timedelta

from ..const import (  # noqa: TID252
    ATTR_ASSET_CLASS,
    ATTR_BACKUP_CURRENT_ASSET,
    ATTR_DISCARD_DASHBOARD_USER_CHANGES,
    ATTR_DOWNLOAD_FROM_DEV_BRANCH,
    ATTR_DOWNLOAD_FROM_REPO,
    DOMAIN,
    VA_ADD_UPDATE_ENTITY_EVENT,
    VERSION_CHECK_INTERVAL,
)
from ..typed import VAConfigEntry  # noqa: TID252
from .base import AssetManagerException, BaseAssetManager
from .blueprints import BlueprintManager
from .dashboard import DashboardManager
from .views import ViewManager

_LOGGER = logging.getLogger(__name__)

ASSETS_MANAGER = "assets_manager"


class AssetClass(StrEnum):
    """Asset class."""

    DASHBOARD = "dashboard"
    VIEW = "views"
    BLUEPRINT = "blueprints"


# Dashboard must be listed first to ensure it is loaded/created
# first during onboarding
ASSET_CLASS_MANAGERS = {
    AssetClass.DASHBOARD: DashboardManager,
    AssetClass.VIEW: ViewManager,
    AssetClass.BLUEPRINT: BlueprintManager,
}

LOAD_ASSET_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ASSET_CLASS): vol.In(
            [AssetClass.DASHBOARD, AssetClass.VIEW, AssetClass.BLUEPRINT]
        ),
        vol.Required(ATTR_NAME): str,
        vol.Required(ATTR_DOWNLOAD_FROM_REPO, default=False): bool,
        vol.Required(ATTR_DOWNLOAD_FROM_DEV_BRANCH, default=False): bool,
        vol.Required(ATTR_DISCARD_DASHBOARD_USER_CHANGES, default=False): bool,
        vol.Required(ATTR_BACKUP_CURRENT_ASSET, default=False): bool,
    }
)

SAVE_ASSET_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ASSET_CLASS): vol.In([AssetClass.VIEW, AssetClass.BLUEPRINT]),
        vol.Required(ATTR_NAME): str,
    }
)


class AssetsManagerStorage:
    """Class to manager timer store."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        self.hass = hass
        self.data: dict[str, Any] = {}
        self.store = Store(hass, 1, f"{DOMAIN}.assets")
        self.lock = asyncio.Lock()

    async def _save(self):
        """Save store."""
        await self.lock.acquire()
        self.data["last_updated"] = dt_util.now().isoformat()
        # Order dict for reading
        data = self.data.copy()
        last_updated = data.pop("last_updated")
        if data.get("last_commit"):
            last_commit = data.pop("last_commit")
        else:
            last_commit = {}
        data = {
            "last_updated": last_updated,
            "last_commit": last_commit,
            **dict(sorted(data.items(), key=lambda x: x[0].lower())),
        }
        await self.store.async_save(data)
        self.lock.release()

    async def load(self, force: bool = False):
        """Load dashboard data from store."""
        if self.data and not force:
            return self.data
        try:
            if data := await self.store.async_load():
                self.data = data
            else:
                self.data = {}
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Error loading asset store. Error is %s", ex)
            self.data = {}
        return self.data

    async def update(self, asset_class: str, id: str | None, data: dict[str, Any]):
        """Update store."""
        self.data.setdefault(asset_class, {})
        if id is not None:
            self.data[asset_class][id] = data
        else:
            self.data[asset_class] = data
        await self._save()

    async def update_last_commit(self, asset_class: str, last_commit: str):
        """Update last commit date."""
        self.data.setdefault("last_commit", {})
        self.data["last_commit"][asset_class] = last_commit
        await self._save()


class AssetsManager:
    """Class to manage VA asset installs/updates/deletes etc."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.store = AssetsManagerStorage(hass)
        self.managers: dict[str, BaseAssetManager] = {}
        self.data: dict[str, Any] = {}
        self.force_onboard: bool = False

    async def async_setup(self) -> None:
        """Set up the AssetManager."""
        try:
            _LOGGER.debug("Setting up AssetsManager")
            self.data = await self.store.load()

            # Setup managers
            for asset_class, manager in ASSET_CLASS_MANAGERS.items():
                self.managers[asset_class] = manager(
                    self.hass, self.config, self.data.get(asset_class)
                )

            # Onboard managers
            await self.onboard_managers()

            # Setup managers
            for manager in self.managers.values():
                await manager.async_setup()

            # Add update action
            self.hass.services.async_register(
                DOMAIN,
                "update_versions",
                self._async_handle_update_versions_service_call,
            )

            # Add load asset action
            self.hass.services.async_register(
                DOMAIN,
                "load_asset",
                self._async_handle_load_asset_service_call,
                schema=LOAD_ASSET_SERVICE_SCHEMA,
            )

            # Add save asset action
            self.hass.services.async_register(
                DOMAIN,
                "save_asset",
                self._async_handle_save_asset_service_call,
                schema=SAVE_ASSET_SERVICE_SCHEMA,
            )

            # Experimental - schedule update of asset latest versions
            if self.config.runtime_data.integration.enable_updates:
                self.config.async_on_unload(
                    async_track_time_interval(
                        self.hass,
                        self.async_update_version_info,
                        timedelta(minutes=VERSION_CHECK_INTERVAL),
                    )
                )
        except Exception as ex:
            _LOGGER.error("Error setting up AssetsManager. Error is %s", ex)
            raise HomeAssistantError(f"Error setting up AssetsManager: {ex}") from ex

    async def _async_handle_update_versions_service_call(self, call: ServiceCall):
        """Handle update of the view versions."""
        try:
            await self.async_update_version_info(force=True)
        except AssetManagerException as ex:
            raise HomeAssistantError(ex) from ex

    async def _async_handle_load_asset_service_call(self, call: ServiceCall):
        """Handle load of a view from view_assist dir."""

        asset_class = call.data.get(ATTR_ASSET_CLASS)
        asset_name = call.data.get(ATTR_NAME)
        download = call.data.get(ATTR_DOWNLOAD_FROM_REPO, False)
        dev_branch = call.data.get(ATTR_DOWNLOAD_FROM_DEV_BRANCH, False)
        discard_user_dashboard_changes = call.data.get(
            ATTR_DISCARD_DASHBOARD_USER_CHANGES, False
        )
        backup = call.data.get(ATTR_BACKUP_CURRENT_ASSET, False)

        try:
            await self.async_install_or_update(
                asset_class,
                asset_name,
                download=download,
                dev_branch=dev_branch,
                discard_user_dashboard_changes=discard_user_dashboard_changes,
                backup_existing=backup,
            )
        except AssetManagerException as ex:
            raise HomeAssistantError(ex) from ex

    async def _async_handle_save_asset_service_call(self, call: ServiceCall):
        """Handle save of a view to view_assist dir."""

        asset_class = call.data.get(ATTR_ASSET_CLASS)
        asset_name = call.data.get(ATTR_NAME)

        try:
            if manager := self.managers.get(asset_class):
                await manager.async_save(asset_name)
        except AssetManagerException as ex:
            raise HomeAssistantError(ex) from ex

    async def onboard_managers(self) -> None:
        """Onboard the user if not yet setup."""
        # Check dashboard has not been deleted.
        # Ensures re-onboarded if it has
        if not self.managers[AssetClass.DASHBOARD].is_installed("dashboard"):
            _LOGGER.debug("Dashboard not installed, forcing onboarding")
            self.force_onboard = True

        # Check if onboarding is needed and if so, run it
        for asset_class, manager in self.managers.items():
            if not self.data.get(asset_class) or self.force_onboard:
                _LOGGER.debug("Onboarding %s", asset_class)
                result = await manager.async_onboard(force=self.force_onboard)

                if result:
                    _LOGGER.debug("Onboarding result %s - %s", asset_class, result)
                    self.data[asset_class] = result
                    await self.store.update(asset_class, None, result)

    async def async_update_version_info(
        self, asset_class: AssetClass | None = None, force: bool = False
    ) -> None:
        """Update latest versions for assets."""

        # Throttle updates to once every VERSION_CHECK_INTERVAL minutes
        if not force and self.data and "last_updated" in self.data:
            last_updated = dt_util.parse_datetime(self.data["last_updated"])
            if last_updated and dt_util.utcnow() - last_updated - timedelta(
                seconds=30
            ) < timedelta(minutes=VERSION_CHECK_INTERVAL):
                return

        managers = self.managers
        if asset_class and asset_class in self.managers:
            managers = {k: v for k, v in self.managers.items() if k == asset_class}

        for asset_class, manager in managers.items():  # noqa: PLR1704
            # If no key in self.data, return
            if not self.data.get(asset_class):
                _LOGGER.debug(
                    "No data for %s, skipping update",
                    asset_class,
                )
                continue

            # Reduces download by only getting version from repo if the last commit date is greater than
            # we have stored
            update_from_repo = True
            if self.data.get("last_commit"):
                if repo_last_commit := await manager.async_get_last_commit():
                    stored_last_commit = self.data.get("last_commit").get(asset_class)
                    if repo_last_commit == stored_last_commit:
                        _LOGGER.debug(
                            "No new updates in repo for %s",
                            asset_class,
                        )
                        update_from_repo = False

            if update_from_repo:
                repo_last_commit = await manager.async_get_last_commit()
                _LOGGER.debug("New updates in repo for %s", asset_class)
                self.data.setdefault("last_commit", {})
                self.data["last_commit"][asset_class] = repo_last_commit
                await self.store.update_last_commit(asset_class, repo_last_commit)

            if version_info := await manager.async_get_version_info(update_from_repo):
                for name, versions in version_info.items():
                    self.data[asset_class][name] = versions
                    # Fire update entity update event
                    if versions["installed"]:
                        self._fire_updates_update(
                            asset_class,
                            name,
                            AwesomeVersion(versions["installed"]) >= versions["latest"],
                        )

            await self.store.update(asset_class, None, self.data[asset_class])
        _LOGGER.debug("Latest versions updated")

    async def get_installed_version(self, asset_class: AssetClass, name: str) -> str:
        """Get version info for asset."""
        if self.managers.get(asset_class):
            return await self.managers[asset_class].async_get_installed_version(name)
        return None

    async def async_install_or_update(
        self,
        asset_class: str,
        name: str,
        download: bool = False,
        dev_branch: bool = False,
        discard_user_dashboard_changes: bool = False,
        backup_existing: bool = False,
    ):
        """Install asset."""
        if manager := self.managers.get(asset_class):
            # Install the asset
            status = await manager.async_install_or_update(
                name,
                download=download,
                dev_branch=dev_branch,
                discard_user_dashboard_changes=discard_user_dashboard_changes,
                backup_existing=backup_existing,
            )

            if status.installed:
                # Update the store with the new version
                self.data[asset_class][name] = {
                    "installed": status.version,
                    "latest": status.latest_version,
                }
                await self.store.update(asset_class, name, self.data[asset_class][name])

    def _fire_updates_update(
        self, asset_class: AssetClass, name: str, remove: bool
    ) -> None:
        """Fire update entity update event."""
        async_dispatcher_send(
            self.hass,
            VA_ADD_UPDATE_ENTITY_EVENT,
            {"asset_class": asset_class, "name": name, "remove": remove},
        )
