import asyncio
import dataclasses
import itertools
import logging
from collections.abc import Mapping
from json import dumps as json_dumps
from typing import Any, Self

import aiofiles
from aiohttp import ClientSession, ClientTimeout, FormData
from multidict import CIMultiDict
from yarl import URL

from bunkr_uploader.api import responses
from bunkr_uploader.api.errors import ChunkUploadError, FileUploadError
from bunkr_uploader.api.file import Chunk, File

_API_ENTRYPOINT = URL("https://dash.bunkr.cr/api/")
_SEMAPHORE = asyncio.Semaphore(50)


__all__ = ["BunkrAPI"]
logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class BunkrAPI:
    token: str
    chunk_size: int = 0
    _session: ClientSession | None = dataclasses.field(init=False, default=None)
    _info: responses.Info | None = dataclasses.field(init=False, default=None)

    async def connect(self) -> None:
        info = await self.check()
        self.chunk_size = self.chunk_size or info.chunkSize.max
        assert 0 < self.chunk_size <= info.chunkSize.max
        await self.verify_token()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            self._session = ClientSession(
                _API_ENTRYPOINT,
                headers={
                    "Accept": "application/json, text/plain",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0",
                    "Referer": "https://dash.bunkr.cr/",
                    "striptags": "null",
                    "Origin": "https://dash.bunkr.cr",
                },
                raise_for_status=True,
                timeout=ClientTimeout(sock_connect=30, sock_read=20),
            )
        return self._session

    async def _request(
        self,
        path_or_url: URL | str,
        *,
        form: FormData | None = None,
        headers: Mapping[str, str] | None = None,
        **json: Any,
    ) -> dict[str, Any]:
        method = "POST" if (form or json) else "GET"

        async with (
            _SEMAPHORE,
            self.session.request(
                method,
                path_or_url,
                data=form,
                json=json or None,
                headers=self._prepare_headers(headers),
            ) as resp,
        ):
            data = await resp.json()
            record = {
                "url": resp.url,
                "headers": dict(resp.headers),
                "data": data,
            }
            logger.debug(f"response: \n {json_dumps(record, indent=4, default=str)}")

            return data

    def _prepare_headers(self, headers: Mapping[str, str] | None = None) -> CIMultiDict[str]:
        """Add default headers and transform it to CIMultiDict"""
        combined = CIMultiDict(token=self.token)
        if headers:
            headers = CIMultiDict(headers)
            new: set[str] = set()
            for key, value in headers.items():
                if key in new:
                    combined.add(key, value)
                else:
                    combined[key] = value
                    new.add(key)
        return combined

    async def check(self) -> responses.Info:
        if not self._info:
            response = await self._request("check")
            self._info = responses.Info.model_validate(response)
        return self._info

    async def node(self) -> responses.Node:
        response = await self._request("node")
        return responses.Node.model_validate(response)

    async def verify_token(self) -> responses.VerifyToken:
        response = await self._request("tokens/verify", token=self.token)
        try:
            resp = responses.VerifyToken.model_validate(response)
        except ValueError:
            raise ValueError("Invalid Token") from None
        if not resp.success:
            raise ValueError("Invalid Token")
        return resp

    async def get_albums(self) -> responses.Albums:
        albums: list[responses.AlbumItem] = []
        for page in itertools.count(0):
            response = await self._request(f"albums/{page}")
            new_albums = response["albums"]
            albums.extend(new_albums)
            if new_albums < 50:
                break

        return responses.Albums.model_validate({"albums": albums, "count": len(albums)})

    async def create_album(
        self,
        name: str,
        *,
        description: str = "",
        public: bool = True,
        download: bool = True,
    ) -> responses.CreateAlbum:
        response = await self._request(
            "albums",
            name=name,
            description=description,
            public=public,
            download=download,
        )
        return responses.CreateAlbum.model_validate(response)

    async def direct_upload(
        self, file: File, server: URL, album_id: str | None = None
    ) -> responses.UploadResponse:
        file.album_id = album_id = file.album_id or album_id
        info = await self.check()
        assert file.size <= info.maxSize
        assert self.chunk_size
        try:
            async with aiofiles.open(file.path, "rb") as file_data:
                chunk_data = await file_data.read(self.chunk_size)

            form = FormData()
            form.add_field(
                "files[]", chunk_data, filename=file.path.name, content_type=file.mimetype
            )
            headers = {"albumid": album_id} if album_id else None
            response = await self._request(server / "upload", form=form, headers=headers)

        except Exception as e:
            raise FileUploadError(file) from e

        result = responses.UploadResponse.model_validate(response)
        if not result.success:
            raise FileUploadError(file)
        return result

    async def upload_chunk(self, file: File, server: URL, chunk: Chunk) -> None:
        try:
            form = self._create_chunk_form(file, chunk)
            result = await self._request(server / "upload", form=form)
        except Exception as e:
            raise ChunkUploadError(file, chunk) from e

        if not result["success"]:
            raise ChunkUploadError(file, chunk)

    def _create_chunk_form(self, file: File, chunk: Chunk) -> FormData:
        form = FormData()
        form.add_fields(
            ("dzuuid", file.uuid),
            ("dzchunkindex", str(chunk.index)),
            ("dztotalfilesize", str(file.size)),
            ("dzchunksize", str(self.chunk_size)),
            ("dztotalchunkcount", str(chunk.total)),
            ("dzchunkbyteoffset", str(chunk.offset)),
        )
        form.add_field(
            "files[]",
            chunk.data,
            filename=file.upload_name,
            content_type="application/octet-stream",
        )
        return form

    async def finish_chunks(
        self, file: File, server: URL, album_id: str | None = None
    ) -> responses.UploadResponse:
        file.album_id = album_id = file.album_id or album_id
        payload = {
            "uuid": file.uuid,
            "original": file.original_name,
            "type": file.mimetype,
            "albumid": album_id or None,
            "filelength": None,
            "age": None,
        }
        for _ in range(2):
            try:
                response = await self._request(server / "upload/finishchunks", files=[payload])
                break
            except Exception:
                logger.exception("")
                continue
        else:
            raise FileUploadError(file)

        result = responses.UploadResponse.model_validate(response)
        if not result.success:
            raise FileUploadError(file)
        return result
