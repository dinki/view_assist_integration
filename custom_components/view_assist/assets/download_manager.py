"""Download manager for View Assist assets."""

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any
import urllib.parse

from aiohttp import ContentTypeError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import DOMAIN, GITHUB_BRANCH, GITHUB_REPO  # noqa: TID252

_LOGGER = logging.getLogger(__name__)

GITHUB_TOKEN_FILE = "github.token"
MAX_DIR_DEPTH = 5


class AssetManagerException(Exception):
    """A asset manager exception."""


@dataclass
class GithubFileDir:
    """A github dir or file object."""

    name: str
    type: str
    path: str
    download_url: str | None = None


class GithubAPIException(Exception):
    """A github api exception."""


class GithubRateLimitException(GithubAPIException):
    """A github rate limit exception."""


class GithubNotFoundException(GithubAPIException):
    """A github not found exception."""


class GitHubAPI:
    """Class to handle basic Github repo rest commands."""

    def __init__(
        self, hass: HomeAssistant, repo: str, branch: str = GITHUB_BRANCH
    ) -> None:
        """Initialise."""
        self.hass = hass
        self.repo = repo
        self.branch = branch
        self.api_base = f"https://api.github.com/repos/{self.repo}"
        self.path_base = f"https://github.com/{self.repo}/tree/{self.branch}"
        self.raw_base = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}"

    def _get_token(self):
        # Use HACs token if available
        if hacs := self.hass.data.get("hacs", {}):
            try:
                return hacs.configuration.token
            except AttributeError:
                _LOGGER.debug("HACS is installed but token not available")
        # Otherwise use the token file in the config directory if exists
        token_file = self.hass.config.path(f"{DOMAIN}/{GITHUB_TOKEN_FILE}")
        if Path(token_file).exists():
            with Path(token_file).open("r", encoding="utf-8") as f:
                return f.read()
        return None

    async def _rest_request(
        self, url: str, data_as_text: bool = False
    ) -> str | dict | list | None:
        """Return rest request data."""
        session = async_get_clientsession(self.hass)

        kwargs = {}
        if self.api_base in url:
            if token := await self.hass.async_add_executor_job(self._get_token):
                kwargs["headers"] = {"authorization": f"Bearer {token}"}
                # _LOGGER.debug("Making api request with auth token - %s", url)
            # else:
            #    _LOGGER.debug("Making api request without auth token - %s", url)

        async with session.get(url, **kwargs) as resp:
            if resp.status == 200:
                try:
                    return await resp.json()
                except ContentTypeError:
                    if data_as_text:
                        return await resp.text()
                    return await resp.read()

            elif resp.status == 403:
                # Rate limit
                raise GithubRateLimitException(
                    "Github api rate limit exceeded for this hour.  You may need to add a personal access token to authenticate and increase the limit"
                )
            elif resp.status == 404:
                raise GithubNotFoundException(
                    f"Path not found on this repository.  {url}"
                )
            else:
                raise GithubAPIException(await resp.json())
        return None

    async def async_get_last_commit(self, path: str) -> dict[str, Any] | None:
        """Get the last commit id for a file."""
        try:
            url = f"{self.api_base}/commits?path={path}&per_page=1"
            # _LOGGER.debug("Getting last commit for %s", url)
            if raw_data := await self._rest_request(url):
                if isinstance(raw_data, list):
                    # If the url is a directory, get the first file
                    if raw_data and isinstance(raw_data[0], dict):
                        return raw_data[0]
                return raw_data
        except GithubAPIException as ex:
            _LOGGER.error(ex)
        return None

    async def validate_path(self, path: str) -> bool:
        """Check if a path exists in the repo."""
        try:
            url = f"{self.path_base}/{path}"
            await self._rest_request(url)
        except GithubNotFoundException:
            _LOGGER.debug("Path not found: %s", path)
            return False
        except GithubAPIException as ex:
            _LOGGER.error("Error validating path.  Error is %s", ex)
            return False
        else:
            return True

    async def get_dir_listing(self, path: str) -> list[GithubFileDir]:
        """Get github repo dir listing."""

        path = urllib.parse.quote(path)
        url_path = f"{self.api_base}/contents/{path}?ref={self.branch}"

        try:
            if raw_data := await self._rest_request(url_path):
                return [
                    GithubFileDir(e["name"], e["type"], e["path"], e["download_url"])
                    for e in raw_data
                ]
        except GithubAPIException as ex:
            _LOGGER.error(ex)
        return None

    async def get_file_contents(
        self, path: str, data_as_text: bool = False
    ) -> bytes | None:
        """Download file."""
        path = urllib.parse.quote(path)
        url_path = f"{self.raw_base}/{path}?ref={self.branch}"

        if file_data := await self._rest_request(url_path, data_as_text=data_as_text):
            return file_data
        _LOGGER.debug("Failed to download file")
        return None


class DownloadManager:
    """Class to handle file downloads from github repo."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        self.hass = hass
        self.github = GitHubAPI(hass, GITHUB_REPO, GITHUB_BRANCH)

    def set_branch(self, branch: str) -> None:
        """Set the branch to use for downloads."""
        self.github = GitHubAPI(self.hass, GITHUB_REPO, branch)

    def _save_binary_to_file(self, data: bytes, file_path: str, file_name: str):
        """Save binary data to file."""
        Path(file_path).mkdir(parents=True, exist_ok=True)
        Path.write_bytes(Path(file_path, file_name), data)

    async def async_dir_exists(self, dir_url: str) -> bool:
        """Check if a directory exists."""
        try:
            return await self.github.validate_path(dir_url)
        except GithubAPIException:
            return False

    async def async_get_dir_listing(self, dir_url: str) -> list[GithubFileDir] | None:
        """Get github repo dir listing."""
        try:
            if dir_listing := await self.github.get_dir_listing(dir_url):
                return dir_listing
        except GithubAPIException as ex:
            raise AssetManagerException(
                f"Error getting directory listing for {dir_url}.  Error is {ex}"
            ) from ex
        return None

    async def async_download_dir(
        self, download_dir_path: str, save_path: str, depth: int = 1
    ) -> bool:
        """Download all files in a directory."""
        try:
            if dir_listing := await self.github.get_dir_listing(download_dir_path):
                _LOGGER.debug("Downloading %s", download_dir_path)
                # Recurse directories
                for entry in dir_listing:
                    if entry.type == "dir" and depth <= MAX_DIR_DEPTH:
                        await self.async_download_dir(
                            entry.path,
                            f"{save_path}/{entry.name}",
                            depth=depth + 1,
                        )
                    elif entry.type == "file":
                        _LOGGER.debug(
                            "Downloading file %s", f"{download_dir_path}/{entry.name}"
                        )
                        if file_data := await self.github.get_file_contents(
                            entry.path, data_as_text=False
                        ):
                            await self.hass.async_add_executor_job(
                                self._save_binary_to_file,
                                file_data,
                                save_path,
                                entry.name,
                            )
                        else:
                            raise AssetManagerException(
                                f"Error downloading {entry.name} from the github repository."
                            )
                return True
        except GithubAPIException as ex:
            raise AssetManagerException(
                f"Error downloading {download_dir_path} from the github repository.  Error is {ex}"
            ) from ex
        else:
            return False

    async def get_file_contents(self, file_path: str) -> str | None:
        """Get the contents of a file."""
        try:
            if file_data := await self.github.get_file_contents(
                file_path, data_as_text=True
            ):
                return file_data
        except GithubAPIException as ex:
            raise AssetManagerException(
                f"Error downloading {file_path} from the github repository.  Error is {ex}"
            ) from ex
        return None

    async def get_last_commit_id(self, path: str) -> str | None:
        """Get the last commit date for a file."""
        try:
            if commit_data := await self.github.async_get_last_commit(path):
                return commit_data["sha"][:7]
        except GithubAPIException as ex:
            raise AssetManagerException(
                f"Error getting last commit for {path}.  Error is {ex}"
            ) from ex
        return None
