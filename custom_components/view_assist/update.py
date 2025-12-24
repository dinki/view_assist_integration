"""Update entities for HACS."""

from __future__ import annotations

import logging
from typing import Any

from awesomeversion import AwesomeVersion

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant, HomeAssistantError, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .assets import VA_ADD_UPDATE_ENTITY_EVENT, AssetClass, AssetsManager
from .assets.base import AssetManagerException
from .const import (
    DASHBOARD_DIR,
    DASHBOARD_VIEWS_GITHUB_PATH,
    DOMAIN,
    GITHUB_BRANCH,
    GITHUB_REPO,
    VA_ASSET_UPDATE_PROGRESS,
    VIEWS_DIR,
    WIKI_URL,
)
from .typed import VAConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: VAConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up update platform."""
    am = AssetsManager.get(hass)

    async def async_add_remove_update_entity(
        data: dict[str, Any], startup: bool = False
    ) -> None:
        """Add or remove update entity."""
        asset_class: AssetClass = data.get("asset_class")
        name: str = data.get("name")
        remove: bool = data.get("remove")

        unique_id = f"{DOMAIN}_{asset_class}_{name}"
        entity_reg = er.async_get(hass)

        if remove:
            if entity_id := entity_reg.async_get_entity_id("update", DOMAIN, unique_id):
                entity_reg.async_remove(entity_id)
            return

        # Add new update entity
        entity_id = entity_reg.async_get_entity_id("update", DOMAIN, unique_id)
        if not entity_id or startup:
            async_add_entities(
                [
                    VAUpdateEntity(
                        am=am,
                        asset_class=asset_class,
                        name=name,
                    )
                ]
            )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, VA_ADD_UPDATE_ENTITY_EVENT, async_add_remove_update_entity
        )
    )

    # Set update entities on restart
    for asset_class in AssetClass:
        if not am.data or am.data.get(asset_class) is None:
            continue

        for name in am.data[asset_class]:
            installed = am.store.data[asset_class][name].get("installed", "0.0.0")
            latest = am.store.data[asset_class][name].get("latest", "0.0.0")

            await async_add_remove_update_entity(
                {
                    "asset_class": asset_class,
                    "name": name,
                    "remove": installed and AwesomeVersion(installed) >= latest,
                },
                startup=True,
            )


class VAUpdateEntity(UpdateEntity):
    """Update entities for repositories downloaded with HACS."""

    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, am: AssetsManager, asset_class: AssetClass, name: str) -> None:
        """Initialize."""
        self.am = am
        self._asset_class = asset_class
        self._name = name

        self._attr_supported_features = (
            (self._attr_supported_features | UpdateEntityFeature.BACKUP)
            if self._asset_class != AssetClass.DASHBOARD
            else self._attr_supported_features
        )

    @property
    def name(self) -> str | None:
        """Return the name."""
        if self._asset_class == AssetClass.DASHBOARD:
            return f"View Assist - {self._name.replace('_', ' ').title()}"
        return f"View Assist - {self._name.replace('_', ' ').title()} {self._asset_class.removesuffix('s')}"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{DOMAIN}_{self._asset_class}_{self._name}"

    @property
    def latest_version(self) -> str:
        """Return latest version of the entity."""
        return self.am.store.data[self._asset_class][self._name]["latest"]

    @property
    def release_url(self) -> str:
        """Return the URL of the release page."""
        base = f"https://github.com/{GITHUB_REPO}/tree/{GITHUB_BRANCH}"
        # GH uses _ but wiki uses - so here's a dirty hack
        name_hyphenated = self._name.replace('_', '-')
        if self._asset_class == AssetClass.DASHBOARD:
            return f"{base}/{DASHBOARD_VIEWS_GITHUB_PATH}/{DASHBOARD_DIR}/dashboard"
        if self._asset_class == AssetClass.VIEW:
            return f"{WIKI_URL}/docs/extend-functionality/{VIEWS_DIR}/{name_hyphenated}#changelog"
        if self._asset_class == AssetClass.BLUEPRINT:
            return (
                f"{WIKI_URL}/docs/extend-functionality/sentences/{name_hyphenated}#changelog"
            )
        return base
    
    @property
    def installed_version(self) -> str:
        """Return downloaded version of the entity."""
        installed = self.am.store.data[self._asset_class][self._name]["installed"]
        return installed or "Not installed"

    @property
    def release_summary(self) -> str | None:
        """Return the release summary."""
        if self._asset_class == AssetClass.DASHBOARD:
            return "<ha-alert alert-type='info'>Updating the dashboard will attempt to keep any changes you have made to it</ha-alert>"
        if self._asset_class == AssetClass.VIEW:
            return "<ha-alert alert-type='warning'>Updating this view will overwrite any changes you have made to it</ha-alert>"
        if self._asset_class == AssetClass.BLUEPRINT:
            return "<ha-alert alert-type='warning'>Updating this blueprint will overwrite any changes you have made to it</ha-alert>"
        return None

    @property
    def entity_picture(self) -> str | None:
        """Return the entity picture to use in the frontend."""
        return f"https://brands.home-assistant.io/_/{DOMAIN}/icon.png"

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install an update."""
        try:
            await self.am.async_install_or_update(
                self._asset_class,
                self._name,
                download=True,
                backup_existing=backup,
            )
        except AssetManagerException as exception:
            raise HomeAssistantError(exception) from exception

    async def async_release_notes(self) -> str | None:
        """Return the release notes."""
        # TODO: Get release notes from markdown readme
        return self.release_summary

    async def async_added_to_hass(self) -> None:
        """Register for status events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                VA_ASSET_UPDATE_PROGRESS,
                self._update_download_progress,
            )
        )

    @callback
    def _update_download_progress(self, data: dict) -> None:
        """Update the download progress."""
        if data["name"] != self._name:
            return
        self._attr_in_progress = data["progress"]
        self.async_write_ha_state()
