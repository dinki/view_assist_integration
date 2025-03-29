"""Manage views - download, apply, backup, restore."""

from dataclasses import dataclass
import logging
import operator
from os import PathLike
from pathlib import Path
from typing import Any
import urllib.parse

from aiohttp import ContentTypeError

from homeassistant.components.lovelace import (
    CONF_ICON,
    CONF_REQUIRE_ADMIN,
    CONF_SHOW_IN_SIDEBAR,
    CONF_TITLE,
    CONF_URL_PATH,
    LovelaceData,
    dashboard,
)
from homeassistant.const import (
    CONF_ID,
    CONF_MODE,
    CONF_TYPE,
    EVENT_LOVELACE_UPDATED,
    EVENT_PANELS_UPDATED,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.yaml import load_yaml_dict, save_yaml

from .const import (
    COMMUNITY_VIEWS_DIR,
    DASHBOARD_DIR,
    DASHBOARD_NAME,
    DEFAULT_VIEW,
    DEFAULT_VIEWS,
    DOMAIN,
    GITHUB_BRANCH,
    GITHUB_PATH,
    GITHUB_REPO,
    GITHUB_TOKEN_FILE,
    VIEWS_DIR,
    VAConfigEntry,
)
from .helpers import differ_to_json, json_to_dictdiffer
from .utils import dictdiff
from .websocket import MockWSConnection

_LOGGER = logging.getLogger(__name__)

GITHUB_REPO_API = "https://api.github.com/repos"
MAX_DIR_DEPTH = 5
DASHBOARD_FILE = "dashboard.yaml"
DASHBOARD_MANAGER = "dashboard_manager"


@dataclass
class GithubFileDir:
    """A github dir or file object."""

    name: str
    type: str
    url: str
    download_url: str | None = None


class GithubAPIException(Exception):
    """A github api exception."""


class DownloadManagerException(Exception):
    """A download manager exception."""


class DashboardManagerException(Exception):
    """A dashboard manager exception."""


class GitHubAPI:
    """Class to handle basic Github repo rest commands."""

    def __init__(self, hass: HomeAssistant, repo: str) -> None:
        """Initialise."""
        self.hass = hass
        self.repo = repo
        self.branch: str = GITHUB_BRANCH
        self.api_base = f"{GITHUB_REPO_API}/{self.repo}/contents/"

    def _get_token(self):
        token_file = self.hass.config.path(f"{DOMAIN}/{GITHUB_TOKEN_FILE}")
        if Path(token_file).exists():
            with Path(token_file).open("r", encoding="utf-8") as f:
                return f.read()
        return None

    async def _rest_request(self, url: str) -> str | dict | list | None:
        """Return rest request data."""
        session = async_get_clientsession(self.hass)

        kwargs = {}
        if self.api_base in url:
            if token := await self.hass.async_add_executor_job(self._get_token):
                kwargs["headers"] = {"authorization": f"Bearer {token}"}
                _LOGGER.debug("Making api request with auth token - %s", url)
            else:
                _LOGGER.debug("Making api request without auth token - %s", url)

        async with session.get(url, **kwargs) as resp:
            if resp.status == 200:
                try:
                    return await resp.json()
                except ContentTypeError:
                    return await resp.read()
            elif resp.status == 403:
                # Rate limit
                raise GithubAPIException(
                    "Github api rate limit exceeded for this hour.  You may need to add a personal access token to authenticate and increase the limit"
                )
            elif resp.status == 404:
                raise GithubAPIException(f"Path not found on this repository.  {url}")
            else:
                raise GithubAPIException(await resp.json())
        return None

    async def get_dir_listing(self, dir_url: str) -> list[GithubFileDir]:
        """Get github repo dir listing."""
        # if dir passed is full url

        base_url = f"{GITHUB_REPO_API}/{self.repo}/contents/"
        if not dir_url.startswith(base_url):
            dir_url = urllib.parse.quote(dir_url)
            url_path = (
                f"{GITHUB_REPO_API}/{self.repo}/contents/{dir_url}?ref={self.branch}"
            )
        else:
            url_path = dir_url

        try:
            if raw_data := await self._rest_request(url_path):
                return [
                    GithubFileDir(e["name"], e["type"], e["url"], e["download_url"])
                    for e in raw_data
                ]
        except GithubAPIException as ex:
            _LOGGER.error(ex)
        return None

    async def download_file(self, download_url: str) -> bytes | None:
        """Download file."""
        if file_data := await self._rest_request(download_url):
            return file_data
        _LOGGER.debug("Failed to download file")
        return None


class DownloadManager:
    """Class to handle file downloads from github repo."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        self.hass = hass
        self.github = GitHubAPI(hass, GITHUB_REPO)

    def _save_binary_to_file(self, data: bytes, file_path: str, file_name: str):
        """Save binary data to file."""
        Path(file_path).mkdir(parents=True, exist_ok=True)
        Path.write_bytes(Path(file_path, file_name), data)

    async def _download_dir(self, dir_url: str, dir_path: str, depth: int = 1) -> bool:
        """Download all files in a directory."""
        try:
            if dir_listing := await self.github.get_dir_listing(dir_url):
                _LOGGER.debug("Downloading %s", dir_url)
                # Recurse directories
                for entry in dir_listing:
                    if entry.type == "dir" and depth <= MAX_DIR_DEPTH:
                        await self._download_dir(
                            f"{dir_url}/{entry.name}",
                            f"{dir_path}/{entry.name}",
                            depth=depth + 1,
                        )
                    elif entry.type == "file":
                        _LOGGER.debug("Downloading file %s", f"{dir_url}/{entry.name}")
                        if file_data := await self.github.download_file(
                            entry.download_url
                        ):
                            await self.hass.async_add_executor_job(
                                self._save_binary_to_file,
                                file_data,
                                dir_path,
                                entry.name,
                            )
                        else:
                            raise DownloadManagerException(
                                f"Error downloading {entry.name} from the github repository."
                            )
                return True
        except GithubAPIException as ex:
            _LOGGER.error(ex)
        else:
            return False

    async def download_dashboard(self):
        """Download dashboard file."""
        # Ensure download to path exists
        base = self.hass.config.path(f"{DOMAIN}/{DASHBOARD_DIR}")

        # Validate view dir on repo
        dir_url = f"{GITHUB_PATH}/{DASHBOARD_DIR}"
        if await self.github.get_dir_listing(dir_url):
            # Download view files
            await self._download_dir(dir_url, base)

    async def download_view(
        self,
        view_name: str,
        community_view: bool = False,
    ):
        """Download files from a github repo directory."""

        # Ensure download to path exists
        base = self.hass.config.path(f"{DOMAIN}/{VIEWS_DIR}")
        if community_view:
            dir_url = f"{GITHUB_PATH}/{VIEWS_DIR}/{COMMUNITY_VIEWS_DIR}/{view_name}"
            msg_text = "community view"
        else:
            dir_url = f"{GITHUB_PATH}/{VIEWS_DIR}/{view_name}"
            msg_text = "view"

        _LOGGER.debug("Downloading %s - %s", msg_text, view_name)

        # Validate view dir on repo
        if await self.github.get_dir_listing(dir_url):
            # Create view directory
            Path(base, view_name).mkdir(parents=True, exist_ok=True)

            # Download view files
            await self._download_dir(dir_url, Path(base, view_name))


class DashboardManager:
    """Class to manage VA dashboard and views."""

    def __init__(self, hass: HomeAssistant, config: VAConfigEntry) -> None:
        """Initialise."""
        self.hass = hass
        self.config = config
        self.download_manager = DownloadManager(hass)
        self.build_mode: bool = False

        # Experimental - listen for dashboard change and write out changes
        config.async_on_unload(
            hass.bus.async_listen(EVENT_LOVELACE_UPDATED, self._dashboard_changed)
        )

    async def _save_to_yaml_file(
        self,
        file_path: str | PathLike,
        data: dict[str, Any],
    ) -> bool:
        """Save dict to yaml file."""

        await self.hass.async_add_executor_job(
            save_yaml,
            file_path,
            data,
        )
        return True

    @property
    def dashboard_key(self) -> str:
        """Return path for dashboard name."""
        return DASHBOARD_NAME.replace(" ", "-").lower()

    @property
    def dashboard_exists(self) -> bool:
        """Return if dashboard exists."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        return self.dashboard_key in lovelace.dashboards

    async def setup_dashboard(self):
        """Config VA dashboard."""
        if not self.dashboard_exists:
            _LOGGER.debug("Initialising View Assist dashboard")
            self.build_mode = True

            # Download dashboard
            base = self.hass.config.path(DOMAIN)
            dashboard_file_path = f"{base}/{DASHBOARD_DIR}/{DASHBOARD_DIR}.yaml"
            view_base = f"{base}/{VIEWS_DIR}"

            if not Path(dashboard_file_path).exists():
                await self.download_manager.download_dashboard()

            # Download initial views
            _LOGGER.debug("Downloading default views")
            for view in DEFAULT_VIEWS:
                if not Path(view_base, view).exists():
                    await self.download_manager.download_view(view)

            # Load dashboard
            _LOGGER.debug("Adding dashboard")
            await self.add_dashboard(DASHBOARD_NAME, dashboard_file_path)
            await self._apply_user_dashboard_changes()

            # Load views that have successfully downloaded plus others already in directory
            _LOGGER.debug("Adding views")
            for view in await self.hass.async_add_executor_job(Path(view_base).iterdir):
                try:
                    await self.add_update_view(
                        view.name,
                        download_from_repo=False,
                    )
                except DashboardManagerException as ex:
                    _LOGGER.warning(ex)

            # Remove home view
            await self.delete_view("home")

            # Finish
            self.build_mode = False

            # Fire refresh event
            self.hass.bus.async_fire(EVENT_PANELS_UPDATED)

        else:
            _LOGGER.debug(
                "View Assist dashboard already exists, skipping initialisation"
            )

    async def _dashboard_changed(self, event: Event):
        # If in dashboard build mode, ignore changes
        if self.build_mode:
            return

        if event.data["url_path"] == self.dashboard_key:
            try:
                lovelace: LovelaceData = self.hass.data["lovelace"]
                dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
                    self.dashboard_key
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
                        await self._save_to_yaml_file(
                            Path(file_path, "user_dashboard.yaml"),
                            diffs,
                        )
            except Exception as ex:  # noqa: BLE001
                _LOGGER.error("Error saving dashboard. Error is %s", ex)

    async def add_dashboard(self, dashboard_name: str, dashboard_path: str):
        """Create dashboard."""

        if not self.dashboard_exists:
            mock_connection = MockWSConnection(self.hass)
            if mock_connection.execute_ws_func(
                "lovelace/dashboards/create",
                {
                    CONF_ID: 1,
                    CONF_TYPE: "lovelace/dashboards/create",
                    CONF_ICON: "mdi:glasses",
                    CONF_TITLE: dashboard_name,
                    CONF_URL_PATH: self.dashboard_key,
                    CONF_MODE: "storage",
                    CONF_SHOW_IN_SIDEBAR: True,
                    CONF_REQUIRE_ADMIN: False,
                },
            ):
                # Get lovelace (frontend) config data
                lovelace: LovelaceData = self.hass.data["lovelace"]

                # Load dashboard config file from path
                if dashboard_config := await self.hass.async_add_executor_job(
                    load_yaml_dict, dashboard_path
                ):
                    await lovelace.dashboards[self.dashboard_key].async_save(
                        dashboard_config
                    )

    async def update_dashboard(
        self,
        download_from_repo: bool = False,
    ):
        """Download latest dashboard from github repository and apply."""

        # download dashboard - no backup
        if download_from_repo:
            await self.download_manager.download_dashboard()

        # Apply new dashboard to HA
        base = self.hass.config.path(DOMAIN)
        dashboard_file_path = f"{base}/{DASHBOARD_DIR}/{DASHBOARD_DIR}.yaml"

        if not Path(dashboard_file_path).exists():
            # No master dashboard
            return

        # Load dashboard config file from path
        if updated_dashboard_config := await self.hass.async_add_executor_job(
            load_yaml_dict, dashboard_file_path
        ):
            lovelace: LovelaceData = self.hass.data["lovelace"]
            dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
                self.dashboard_key
            )
            # Load dashboard config data
            if dashboard_store:
                dashboard_config = await dashboard_store.async_load(False)

                # Copy views to updated dashboard
                updated_dashboard_config["views"] = dashboard_config.get("views")

                # Apply
                self.build_mode = True
                await dashboard_store.async_save(updated_dashboard_config)
                await self._apply_user_dashboard_changes()
                self.build_mode = False

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
            if operator.eq(master_dashboard, comp_dash):
                _LOGGER.debug("They are the same!")
            else:
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
                self.dashboard_key
            )
            # Load dashboard config data
            if dashboard_store:
                dashboard_config = await dashboard_store.async_load(False)

                # Apply
                user_changes = json_to_dictdiffer(user_dashboard)
                updated_dashboard = dictdiff.patch(user_changes, dashboard_config)
                await dashboard_store.async_save(updated_dashboard)

    async def view_exists(self, view: str) -> int:
        """Return index of view if view exists."""
        lovelace: LovelaceData = self.hass.data["lovelace"]
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self.dashboard_key
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

    async def add_update_view(
        self,
        name: str,
        download_from_repo: bool = False,
        community_view: bool = False,
        backup_current_view: bool = False,
    ) -> bool:
        """Load a view file into the dashboard from the view_assist view folder."""

        # Block trying to download the community contributions folder
        if name == COMMUNITY_VIEWS_DIR:
            raise DashboardManagerException(
                f"{name} is not not a valid view name.  Please select a view from within that folder"  # noqa: S608
            )

        # Return 1 based view index.  If 0, view doesn't exist
        view_index = await self.view_exists(name)

        if download_from_repo:
            result = await self.download_manager.download_view(
                name,
                community_view=community_view,
            )
            if not result:
                raise DashboardManagerException(f"Failed to download {name} view")

        # Install view from file.
        try:
            file_path = Path(self.hass.config.path(DOMAIN), VIEWS_DIR, name)

            # Load in order of existence - user view version (for later feature), default version, saved version
            file: Path
            file_options = [f"user_{name}.yaml", f"{name}.yaml", f"{name}.saved.yaml"]

            if download_from_repo:
                file = Path(file_path, f"{name}.yaml")

            for file_option in file_options:
                if Path(file_path, file_option).exists():
                    file = Path(file_path, file_option)
                    break

            if file:
                _LOGGER.debug("Loading view %s from %s", name, file)
                new_view_config = await self.hass.async_add_executor_job(
                    load_yaml_dict, file
                )
            else:
                raise DashboardManagerException(
                    f"Unable to load view {name}.  Unable to find a yaml file"
                )
        except OSError as ex:
            raise DashboardManagerException(
                f"Unable to load view {name}.  Error is {ex}"
            ) from ex

        # Get lovelace (frontend) config data
        lovelace: LovelaceData = self.hass.data["lovelace"]
        # Get access to dashboard store
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self.dashboard_key
        )

        # Load dashboard config data
        if new_view_config and dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)

            # Save existing view to file
            if backup_current_view:
                await self.save_view(name)

            _LOGGER.debug("Adding view %s to dashboard", name)
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
            modified = True

            # Save dashboard config back to HA
            if modified:
                self.build_mode = True
                await dashboard_store.async_save(dashboard_config)
                self.hass.bus.async_fire(EVENT_PANELS_UPDATED)
                self.build_mode = False
            return True
        return False

    async def save_view(self, view_name: str) -> bool:
        """Backup a view to a file."""

        # Get lovelace (frontend) config data
        lovelace: LovelaceData = self.hass.data["lovelace"]

        # Get access to dashboard store
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self.dashboard_key
        )

        # Load dashboard config data
        if dashboard_store:
            dashboard_config = await dashboard_store.async_load(False)

            # Make list of existing view names for this dashboard
            for view in dashboard_config["views"]:
                if view.get("path") == view_name.lower():
                    file_path = Path(
                        self.hass.config.path(DOMAIN), VIEWS_DIR, view_name.lower()
                    )
                    file_name = f"{view_name.lower()}.saved.yaml"

                    if view.get("cards", []):
                        # Ensure path exists
                        file_path.mkdir(parents=True, exist_ok=True)
                        return await self._save_to_yaml_file(
                            Path(file_path, file_name),
                            view.get("cards", [])[0],
                        )
                    raise DashboardManagerException(
                        f"No view data to save for {view_name} view"
                    )
        return False

    async def delete_view(self, view: str):
        """Delete view."""

        # Get lovelace (frontend) config data
        lovelace: LovelaceData = self.hass.data["lovelace"]

        # Get access to dashboard store
        dashboard_store: dashboard.LovelaceStorage = lovelace.dashboards.get(
            self.dashboard_key
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
