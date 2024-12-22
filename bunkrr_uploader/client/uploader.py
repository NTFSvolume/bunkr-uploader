import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
from aiohttp import ClientSession, FormData
from pydantic import ByteSize
from yarl import URL

from bunkrr_uploader.api import BunkrrAPI
from bunkrr_uploader.api.types.files import ChunkInfo, FileInfo
from bunkrr_uploader.api.types.responses import UploadItemResponse, UploadResponse
from bunkrr_uploader.client.errors import FileUploadError

logger = logging.getLogger(__name__)


class BunkrrUploader:
    def __init__(
        self,
        token: str,
        *,
        max_connections: int = 1,
        chunk_size: ByteSize | None = None,
        upload_retries: int = 1,
        chunk_retries: int = 2,
        upload_delay: float = 0.5,
        **kwargs: dict,
    ):
        self._api = BunkrrAPI(token, chunk_size)
        assert max_connections <= self._api.RATE_LIMIT
        self._semaphore = asyncio.Semaphore(max_connections)
        self._upload_retries = upload_retries
        self._chunk_retries = chunk_retries
        self.options = kwargs
        self._upload_delay = upload_delay
        self._ready = False

    async def startup(self):
        await self._api.startup()
        self._chunk_size = self._api._chunk_size
        self._ready = True

    def _prepare_files(self, files: list[Path]) -> list[FileInfo]:
        files_to_upload = []
        for file in files:
            file_info = FileInfo(file)
            if file.suffix.casefold() in self._api.info.stripTags.blacklistExtensions:
                logger.error(f"File {file} has blacklisted extension {file.suffix}")

            elif file_info.size > self._api.info.maxSize:
                msg = f"File {file} is bigger than max file size {self._api.info.maxSize.human_readable(decimal=True)}"
                logger.error(msg)
            else:
                files_to_upload.append(file_info)

        return files_to_upload

    async def _upload_chunk(self, file_info: FileInfo, chunk: ChunkInfo, server: URL) -> bool:
        """Upload a single chunk with retry mechanism."""
        dzchunkbyteoffset = self._chunk_size * chunk.index
        for attempt in range(self._chunk_retries):
            data = FormData()
            data.add_fields(
                ("dzuuid", file_info.uuid),
                ("dzchunkindex", str(chunk.index)),
                ("dztotalfilesize", str(file_info.size)),
                ("dzchunksize", str(self._chunk_size)),
                ("dztotalchunkcount", str(chunk.total)),
                ("dzchunkbyteoffset", str(dzchunkbyteoffset)),
            )
            data.add_field(
                "files[]",
                chunk.data,
                filename=file_info.upload_name,
                content_type="application/octet-stream",
            )
            try:
                response = await self._api._post("/upload", data=data, server=server)
                response = UploadResponse(**response)
                return response.success

            except Exception as e:
                if attempt < self._chunk_retries - 1:
                    msg = f"{file_info.uuid} failed uploading chunk #{chunk.index}/{chunk.total} to {server} [{attempt+1}/{self._chunk_retries}]"
                    logger.error(msg)
                    await asyncio.sleep(self._upload_delay)
                    continue
                raise FileUploadError(file_info) from e
        return False

    async def _iter_chunks_read(self, file: FileInfo) -> AsyncIterator[ChunkInfo]:
        """Iterate over file chunks using read method."""
        total_chunks = (file.size + self._chunk_size - 1) // self._chunk_size
        async with aiofiles.open(file.path, mode="rb") as file_data:
            index = 0
            while True:
                chunk_data = await file_data.read(self._chunk_size)
                if not chunk_data:
                    break
                yield ChunkInfo(chunk_data, index, total_chunks)
                index += 1

    async def _upload_file(self, file_info: FileInfo, server: URL) -> UploadResponse:
        """Upload a file in chunks with retry mechanism."""
        finish_chunks = False
        for attempt in range(self._upload_retries):
            try:
                if file_info.size <= self._chunk_size:
                    return await self._api.upload(file_info, server)
                elif not finish_chunks:
                    async for chunk in self._iter_chunks_read(file_info):
                        await self._upload_chunk(file_info, chunk, server)
                    finish_chunks = True
                if finish_chunks:
                    return await self._api.finish_chunks(file_info)

            except Exception as e:
                if attempt < self._upload_retries - 1:
                    msg = f"{file_info.path} upload failed [{attempt+1}/{self._upload_retries}]"
                    logger.error(msg)
                    await asyncio.sleep(self._upload_delay)
                    continue

                raise FileUploadError(file_info) from e

        item = UploadItemResponse(name=file_info.path.name, url=None, success=False)
        return UploadResponse(success=False, files=[item])

    async def _get_server(self, album_id: int | None = None) -> URL | None:
        node_response = await self._api.get_node()
        if not node_response.success:
            return None
        server: URL = node_response.url  # type: ignore
        if server not in self._api.server_sessions:
            headers = {"albumid": album_id} if album_id else {}
            headers = self._api._session_headers | headers
            session = ClientSession(server, headers=headers)  # type: ignore
            self._api.add_server_session({server: session})

        return server

    async def upload(self, path: Path, recurse: bool = False, album_name: str | None = None) -> list[UploadResponse]:
        if not path.exists():
            raise FileNotFoundError
        if path.is_file():
            files_to_upload = [path]
        elif recurse:
            files_to_upload = sorted([x for x in path.rglob("*") if x.is_file()], key=lambda p: str(p))
        else:
            files_to_upload = sorted([x for x in path.iterdir() if x.is_file()], key=lambda p: str(p))
        if not self._ready:
            await self.startup()
        files_to_upload = self._prepare_files(files_to_upload)
        if not files_to_upload:
            logger.error("No files left to upload")
            return [UploadResponse(success=False, files=[])]

        album_id = None
        if album_name:
            existing_albums = await self._api.get_albums()
            album = next((x for x in existing_albums.albums if x.name == album_name), None)
            if not album:
                logger.debug(f"album '{album_name}' does not exists, creating")
                album = await self._api.create_album(album_name, description=album_name)
            album_id = album.id
            logger.debug(f"album id: '{album_id}'")

        responses = []

        for file_info in files_to_upload:
            default_response = {"success": False, "files": [file_info.dump_json()]}
            server = await self._get_server(album_id)
            if not server:
                responses.append(UploadResponse(**default_response))
                continue
            response = await self._upload_file(file_info, server=server)
            responses.append(response)

        return responses
