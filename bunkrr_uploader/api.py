import asyncio
import logging
from pathlib import Path

import aiofiles
from aiohttp import ClientSession, FormData
from yarl import URL

from bunkrr_uploader.types.files import FileInfo
from bunkrr_uploader.types.responses import (
    AlbumsResponse,
    CheckResponse,
    CreateAlbumResponse,
    NodeResponse,
    UploadResponse,
    VerifyTokenResponse,
)

logger = logging.getLogger(__name__)


class BunkrrAPI:
    RATE_LIMIT = 50

    def __init__(self, token: str, chunk_size: int | None = None):
        self._token = token
        self._api_entrypoint = URL("https://dash.bunkr.cr/api/")
        self._session_headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
            "token": self._token,
        }
        self._session = ClientSession(self._api_entrypoint, headers=self._session_headers)
        self._chunk_size: int = chunk_size  # type: ignore
        self._info = None
        self._semaphore = asyncio.Semaphore(self.RATE_LIMIT)
        self._server_sessions: dict[URL, ClientSession] = {}

    @property
    def info(self) -> CheckResponse:
        return self._info  # type: ignore

    @property
    def server_sessions(self):
        return self._server_sessions

    async def _get_json(self, path: str) -> dict:
        async with self._semaphore, self._session.get(path) as resp:
            resp.raise_for_status()
            response: dict = await resp.json()
            logger.debug(response)
            return response

    async def _post(self, path: str, *, data: FormData | dict | None = None, server: URL | None = None) -> dict:
        data = data or {}
        if isinstance(data, dict):
            data["token"] = data.get("token") or self._token
        session = self.server_sessions.get(server) or self._session  # type: ignore
        async with self._semaphore, session.post(path, data=data) as resp:
            resp.raise_for_status()
            response = await resp.json()
            logger.debug(response)
            return response

    async def startup(self):
        self._info = await self.check()
        self._chunk_size = self._chunk_size or self.info.chunkSize.default
        assert 0 < self._chunk_size <= self.info.chunkSize.max
        await self.verify_token()

    async def close(self):
        if not self._session.closed:
            await self._session.close()
        for server_session in self._server_sessions.values():
            if not server_session.closed:
                await server_session.close()

    """----------------------------------------------------------------------------------------------"""

    def add_server_session(self, server_session: dict[URL, ClientSession]):
        self._server_sessions.update(server_session)

    async def check(self) -> CheckResponse:
        if self._info:
            return self._info
        response = await self._get_json("check")
        return CheckResponse(**response)

    async def get_node(self) -> NodeResponse:
        response = await self._get_json("node")
        return NodeResponse(**response)

    async def verify_token(self, *, token: str | None = None) -> VerifyTokenResponse:
        response = await self._post("tokens/verify", data={"token": token})
        return VerifyTokenResponse(**response)

    async def get_albums(self) -> AlbumsResponse:
        response = await self._get_json("albums")
        return AlbumsResponse(**response)

    async def create_album(
        self,
        name: str,
        *,
        description: str = "",
        public: bool = True,
        download: bool = True,
    ) -> CreateAlbumResponse:
        data = {"name": name, "description": description, "public": public, "download": download}
        response = await self._post("albums", data=data)
        return CreateAlbumResponse(**response)

    async def upload(self, file: FileInfo | Path, server: URL, album_id: str | None = None) -> UploadResponse:
        if isinstance(file, Path):
            file = FileInfo(file, album_id=album_id)
        file_info = file
        assert file_info.size <= self.info.maxSize
        async with aiofiles.open(file_info.path, "rb") as file_data:
            chunk_data = await file_data.read(self._chunk_size)
        data = FormData()
        data.add_field("files[]", chunk_data, filename=file_info.path.name, content_type=file_info.mimetype)
        if album_id:
            data.add_field("albumid", file_info.album_id)

        response = await self._post("upload", data=data, server=server)
        return UploadResponse(**response)

    async def finish_chunks(self, file_info: FileInfo):
        data = {"files": [file_info.dump_json()]}
        response = await self._post("upload/finishchunks", data=data)
        return UploadResponse(**response)
