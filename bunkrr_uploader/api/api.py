import asyncio

from aiohttp import ClientSession, FormData
from yarl import URL

from bunkrr_uploader.api.types.responses import (
    AlbumsResponse,
    CheckResponse,
    CreateAlbumResponse,
    NodeResponse,
    VerifyTokenResponse,
)


class BunkrrAPI:
    RATE_LIMIT = 50
    def __init__(self, token: str):
        self._token = token
        self._api_entrypoint = URL("https://dash.bunkrr.cr/api")
        self._session_headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
            "token": self._token,
        }
        self._session = ClientSession(self._api_entrypoint, headers=self._session_headers)
        self._info = None
        self._semaphore = asyncio.Semaphore(self.RATE_LIMIT)
        self._server_sessions = {}

    @property
    def info(self):
        return self._info

    @property
    def server_sessions(self):
        return self._server_sessions

    async def _get_json(self, path: str) -> dict:
        async with self._semaphore, self._session.get(path) as resp:
            resp.raise_for_status()
            response = await resp.json()
            return response

    async def _post(self, path: str, *, data: FormData | dict | None = None) -> dict:
        data = data or {}
        if isinstance(data, dict):
            data["token"] = data.get("token") or self._token
        async with self._semaphore, self._session.post(path, data=data) as resp:
            resp.raise_for_status()
            response = await resp.json()
            return response

    async def startup(self):
        self._info = await self.check()
        await self.verify_token()

    """----------------------------------------------------------------------------------------------"""

    def add_server_session(self, server_session: dict[str, ClientSession]):
        self._server_sessions.update(server_session)

    async def check(self) -> CheckResponse:
        if self._info:
            return self._info
        response = await self._get_json("/check")
        return CheckResponse(**response)

    async def get_node(self) -> NodeResponse:
        response = await self._get_json("/node")
        return NodeResponse(**response)

    async def verify_token(self,*, token: str | None = None) -> VerifyTokenResponse:
        response = await self._post("/tokens/verify", data = {"token": token})
        return VerifyTokenResponse(**response)

    async def get_albums(self) -> AlbumsResponse:
        response = await self._get_json("/albums")
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
        response = await self._post("/albums", data=data)
        return CreateAlbumResponse(**response)
