import asyncio
import itertools
import json
import logging
from pathlib import Path
from typing import Any, Final, Self

import aiofiles
from aiohttp import ClientResponse, ClientSession, ClientTimeout, FormData
from yarl import URL

from bunkr_uploader.api import _responses
from bunkr_uploader.api._files import File

_logger = logging.getLogger(__name__)
_API_ENTRYPOINT = URL("https://dash.bunkr.cr/api/")
_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/plain",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0",
    "Referer": "https://dash.bunkr.cr/",
    "striptags": "null",
    "Origin": "https://dash.bunkr.cr",
    "Pragma": "no-cache",
}


def _log_resp(resp: ClientResponse, response: Any) -> None:
    record = {
        "url": resp.url,
        "headers": dict(resp.headers),
        "response": response,
    }
    _logger.debug(f"response: \n {json.dumps(record, indent=4, default=str)}")


class BunkrrAPI:
    RATE_LIMIT: Final[int] = 50

    def __init__(self, token: str, chunk_size: int | None = None):
        self._token = token
        self._session_headers = _DEFAULT_HEADERS | {"token": self._token}
        self._chunk_size: int = chunk_size or 0
        self._info: _responses.Check
        self._semaphore = asyncio.Semaphore(self.RATE_LIMIT)
        self.__session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def _session(self) -> ClientSession:
        if self.__session is None:
            self.__session = ClientSession(
                _API_ENTRYPOINT,
                headers=self._session_headers,
                raise_for_status=True,
                timeout=ClientTimeout(sock_connect=30, sock_read=20),
            )
        return self.__session

    async def _request(
        self,
        path_or_url: URL | str,
        *,
        data: FormData | dict[str, str] | None = None,
        json: dict | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        data = data or kwargs or None
        if isinstance(data, dict) and "finishchunks" not in str(path_or_url):
            data["token"] = data.get("token") or self._token

        method = "POST" if (data or json) else "GET"

        headers = self._session_headers | (headers or {})

        async with (
            self._semaphore,
            self._session.request(
                method, path_or_url, data=data, json=json, headers=headers
            ) as resp,
        ):
            response = await resp.json()
            _log_resp(resp, response)
            return response

    async def startup(self) -> None:
        self._info = await self.check()
        self._chunk_size = self._chunk_size or self._info.chunkSize.default
        assert 0 < self._chunk_size <= self._info.chunkSize.max
        await self.verify_token()

    async def close(self) -> None:
        if self.__session is not None:
            await self.__session.close()

    async def check(self) -> _responses.Check:
        if self._info:
            return self._info
        response = await self._request("check")
        return _responses.Check.model_validate(response)

    async def get_node(self) -> _responses.Node:
        response = await self._request("node")
        return _responses.Node.model_validate(response)

    async def verify_token(self, *, token: str | None = None) -> _responses.VerifyToken:
        response = await self._request("tokens/verify", token=token)
        return _responses.VerifyToken.model_validate(response)

    async def get_albums(self) -> _responses.Albums:
        albums: list[_responses.AlbumItem] = []
        for page in itertools.count(0):
            response = await self._request(f"albums/{page}")
            new_albums = response["albums"]
            albums.extend(new_albums)
            if new_albums < 50:
                break

        return _responses.Albums.model_validate({"albums": albums, "count": len(albums)})

    async def create_album(
        self,
        name: str,
        *,
        description: str = "",
        public: bool = True,
        download: bool = True,
    ) -> _responses.CreateAlbum:
        response = await self._request(
            "albums", name=name, description=description, public=public, download=download
        )
        return _responses.CreateAlbum.model_validate(response)

    async def direct_upload(
        self, file_or_path: File | Path, server: URL, album_id: str | None = None
    ) -> _responses.Upload:
        if isinstance(file_or_path, Path):
            file = File.from_path(file_or_path)
        else:
            file = file_or_path

        file.album_id = album_id
        assert file.size <= self._info.maxSize
        async with aiofiles.open(file.path, "rb") as file_data:
            chunk_data = await file_data.read(self._chunk_size)

        data = FormData()
        data.add_field("files[]", chunk_data, filename=file.path.name, content_type=file.mimetype)
        if file.album_id:
            data.add_field("albumid", str(file.album_id))

        response = await self._request(server / "upload", data=data)
        return _responses.Upload.model_validate(response)

    async def finish_chunks(self, file: File, server: URL) -> _responses.Upload:
        payload = {"files": [file.payload()]}
        _logger.info(payload)
        response = await self._request(server / "upload/finishchunks", json=payload)
        return _responses.Upload.model_validate(response)


__all__ = ["BunkrrAPI"]
