"""View Assist Javascript module registration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace import MODE_STORAGE, LovelaceData
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import DOMAIN, JSMODULES, URL_BASE  # noqa: TID252
from ..typed import VAConfigEntry  # noqa: TID252

_LOGGER = logging.getLogger(__name__)

JS_URL = f"/{URL_BASE}/js"


class JSModuleRegistration:
    """Register Javascript modules."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.lovelace: LovelaceData = self.hass.data.get("lovelace")

        # Fix for change to name of mode to resource_mode in 2026.2
        if MAJOR_VERSION >= 2026 and MINOR_VERSION >= 2:
            self.resource_mode = self.lovelace.resource_mode
        else:
            self.resource_mode = self.lovelace.mode

    async def async_setup(self) -> bool:
        """Register view_assist path."""
        # Remove previous registration - can be removed after this version
        await self.async_unregister(URL_BASE)

        await self._async_register_path()
        if self.resource_mode == MODE_STORAGE:
            await self._async_wait_for_lovelace_resources()
        return True

    async def async_unload(self) -> bool:
        """Unload javascript module registration."""
        if self.resource_mode == MODE_STORAGE:
            await self.async_unregister()
        return True

    # install card resources
    async def _async_register_path(self):
        """Register resource path if not already registered."""
        try:
            path = Path(self.hass.config.path(f"custom_components/{DOMAIN}/js_modules"))
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(JS_URL, path, False)]
            )
            _LOGGER.debug("Registered resource path from %s", path)
        except RuntimeError:
            # Runtime error is likley this is already registered.
            _LOGGER.debug("Resource path already registered")

    async def _async_wait_for_lovelace_resources(self) -> None:
        """Wait for lovelace resources to have loaded."""

        async def _check_lovelace_resources_loaded(now):
            if self.lovelace.resources.loaded:
                await self._async_register_modules()
            else:
                _LOGGER.debug(
                    "Unable to install resources because Lovelace resources have not yet loaded.  Trying again in 5 seconds"
                )
                async_call_later(self.hass, 5, _check_lovelace_resources_loaded)

        await _check_lovelace_resources_loaded(0)

    async def _async_register_modules(self):
        """Register modules if not already registered."""
        _LOGGER.debug("Installing javascript modules")

        # Get resources already registered
        resources = [
            resource
            for resource in self.lovelace.resources.async_items()
            if resource["url"].startswith(JS_URL)
        ]

        for module in JSMODULES:
            url = f"{JS_URL}/{module.get('filename')}"

            card_registered = False

            for resource in resources:
                if self._get_resource_path(resource["url"]) == url:
                    card_registered = True
                    # check version
                    if self._get_resource_version(resource["url"]) != module.get(
                        "version"
                    ):
                        # Update card version
                        _LOGGER.debug(
                            "Updating %s to version %s",
                            module.get("name"),
                            module.get("version"),
                        )
                        await self.lovelace.resources.async_update_item(
                            resource.get("id"),
                            {
                                "res_type": "module",
                                "url": url + "?v=" + module.get("version"),
                            },
                        )
                        # Remove old gzipped files
                        await self.async_remove_gzip_files()
                    else:
                        _LOGGER.debug(
                            "%s already registered as version %s",
                            module.get("name"),
                            module.get("version"),
                        )

            if not card_registered:
                _LOGGER.debug(
                    "Registering %s as version %s",
                    module.get("name"),
                    module.get("version"),
                )
                await self.lovelace.resources.async_create_item(
                    {"res_type": "module", "url": url + "?v=" + module.get("version")}
                )

    def _get_resource_path(self, url: str):
        return url.split("?")[0]

    def _get_resource_version(self, url: str):
        try:
            if version := url.split("?")[1].replace("v=", ""):
                return version
        except IndexError:
            pass
        return 0

    async def async_unregister(self, url: str = JS_URL):
        """Unload lovelace module resource."""
        if self.resource_mode == MODE_STORAGE:
            for module in JSMODULES:
                url = f"{url}/{module.get('filename')}"
                resources = [
                    resource
                    for resource in self.lovelace.resources.async_items()
                    if str(resource["url"]).startswith(url)
                ]
                for resource in resources:
                    await self.lovelace.resources.async_delete_item(resource.get("id"))

    async def async_remove_gzip_files(self):
        """Remove cached gzip files."""
        await self.hass.async_add_executor_job(self.remove_gzip_files)

    def remove_gzip_files(self):
        """Remove cached gzip files."""
        path = self.hass.config.path(f"custom_components/{DOMAIN}/js_modules")

        gzip_files = [
            file.name for file in Path(path).iterdir() if file.name.endswith(".gz")
        ]

        for file in gzip_files:
            try:
                if (
                    Path.stat(f"{path}/{file}").st_mtime
                    < Path.stat(f"{path}/{file.replace('.gz', '')}").st_mtime
                ):
                    _LOGGER.debug("Removing older gzip file - %s", file)
                    Path.unlink(f"{path}/{file}")
            except OSError:
                pass
