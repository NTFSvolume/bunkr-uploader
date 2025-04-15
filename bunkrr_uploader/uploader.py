from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

import aiofiles
from aiohttp import ClientSession, FormData
from tqdm.asyncio import tqdm

from bunkrr_uploader.api import BunkrrAPI
from bunkrr_uploader.api.errors import FileUploadError
from bunkrr_uploader.api.files import ChunkInfo, FileInfo
from bunkrr_uploader.api.responses import UploadItemResponse, UploadResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from pydantic import ByteSize
    from yarl import URL

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BunkrUploaderSettings:
    path: Path
    concurrent_uploads: int = 1
    chunk_size: ByteSize | None = None
    upload_retries: int = 1
    use_max_chunk_size: bool = False
    chunk_retries: int = 2
    upload_delay: float = 0.5

    @classmethod
    def update(cls, **kwargs) -> BunkrUploaderSettings:
        cls_fields = fields(cls)
        cls_fields_names = [f.name for f in cls_fields]
        valid_kwargs = {k: v for k, v in kwargs.items() if k in cls_fields_names}
        if not valid_kwargs:
            msg = "None of the provided attribute is in the class"
            raise ValueError(msg)
        return BunkrUploaderSettings(**valid_kwargs)


class BunkrrUploader:
    def __init__(self, token: str, **kwargs):
        settings = BunkrUploaderSettings.update(**kwargs)
        self._api = BunkrrAPI(token, settings.chunk_size)
        self.settings = settings
        assert self.settings.concurrent_uploads <= self._api.RATE_LIMIT
        self._max_connections = asyncio.Semaphore(settings.concurrent_uploads)
        self._upload_retries = settings.upload_retries
        self._chunk_retries = settings.chunk_retries
        self._use_max_chunk_size = settings.use_max_chunk_size
        self._upload_delay = settings.upload_delay
        self._ready = False

    async def startup(self):
        await self._api.startup()
        if self._use_max_chunk_size:
            self._api._chunk_size = self._api.info.chunkSize.max
        self._api._chunk_size = 10 * 1024 * 1024
        self._chunk_size = self._api._chunk_size
        self._ready = True

    def _prepare_files(self, files: list[Path]) -> list[FileInfo]:
        files_to_upload = []
        for file in files:
            file_info = FileInfo(file)
            if file.suffix.casefold() in self._api.info.stripTags["blacklistExtensions"]:
                logger.error(f"File {file} has blacklisted extension {file.suffix}")

            elif file_info.size > self._api.info.maxSize:
                msg = f"File {file} is bigger than max file size: {self._api.info.maxSize.human_readable(decimal=True)}"
                logger.error(msg)
            else:
                files_to_upload.append(file_info)

        return files_to_upload

    async def _upload_chunk(self, file_info: FileInfo, chunk: ChunkInfo, server: URL) -> bool:
        """Upload a single chunk with retry mechanism."""
        for attempt in range(self._chunk_retries):
            data = self._create_chunk_dataform(file_info, chunk)
            try:
                response = await self._api._post("upload", data=data, server=server)
                response = UploadResponse(**response)
                return response.success

            except Exception as e:
                if attempt < self._chunk_retries:
                    msg = f"{file_info.uuid} failed uploading chunk #{chunk.index+1}/{chunk.total} to {server} [{attempt+1}/{self._chunk_retries}]"
                    logger.error(msg, exc_info=True)
                    await asyncio.sleep(self._upload_delay)
                    continue
                raise FileUploadError(file_info) from e
        return False

    def _create_chunk_dataform(self, file_info: FileInfo, chunk: ChunkInfo) -> FormData:
        form = FormData()
        form.add_fields(
            ("dzuuid", file_info.uuid),
            ("dzchunkindex", str(chunk.index)),
            ("dztotalfilesize", str(file_info.size)),
            ("dzchunksize", str(self._chunk_size)),
            ("dztotalchunkcount", str(chunk.total)),
            ("dzchunkbyteoffset", str(chunk.offset)),
        )
        form.add_field(
            "files[]",
            chunk.data,
            filename=file_info.upload_name,
            content_type="application/octet-stream",
        )
        return form

    async def _iter_chunks_read(self, file_info: FileInfo) -> AsyncIterator[ChunkInfo]:
        """Iterate over file chunks."""
        total_chunks = (file_info.size + self._chunk_size - 1) // self._chunk_size
        async with aiofiles.open(file_info.path, mode="rb") as file_data:
            index = 0
            progress_bar = tqdm(total=file_info.size, unit="B", unit_scale=True, desc="Uploading")
            while True:
                chunk_data = await file_data.read(self._chunk_size)
                chunk_offset = self._chunk_size * index
                if not chunk_data:
                    break
                yield ChunkInfo(chunk_data, index, total_chunks, chunk_offset)
                progress_bar.update(len(chunk_data))
                index += 1
            progress_bar.close()

    async def _upload_file(self, file_info: FileInfo, server: URL) -> UploadResponse:
        """Upload a file in chunks with retry mechanism."""
        finish_chunks = False
        for attempt in range(self._upload_retries):
            try:
                if file_info.size <= self._chunk_size:
                    return await self._api.upload(file_info, server)
                if not finish_chunks:
                    async for chunk in self._iter_chunks_read(file_info):
                        await self._upload_chunk(file_info, chunk, server)
                    finish_chunks = True
                if finish_chunks:
                    return await self._api.finish_chunks(file_info, server)

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
        if "upload" in server.path:
            server = server.with_path("/api/")
        logger.info(f"{server = }")
        if server not in self._api.server_sessions:
            headers = {"albumid": album_id} if album_id else {}
            headers = self._api._session_headers | headers
            session = ClientSession(server, headers=headers)  # type: ignore
            self._api.add_server_session({server: session})

        return server

    async def _get_album_id(self, album_name: str) -> int:
        existing_albums = await self._api.get_albums()
        album = next((x for x in existing_albums.albums if x.name == album_name), None)
        if not album:
            msg = f"album '{album_name}' does not exists, creating"
            logger.debug(msg)
            album = await self._api.create_album(album_name, description=album_name)
        return album.id

    """-------------------------------------------------------------------------------------------------"""

    async def upload(self, path: Path, recurse: bool = False, album_name: str | None = None) -> list[UploadResponse]:
        if not path.exists():
            raise FileNotFoundError
        if path.is_file():
            files_to_upload = [path]
        elif recurse:
            files_to_upload = path.rglob("*")
        else:
            files_to_upload = path.iterdir()

        files_to_upload = sorted([x for x in files_to_upload if x.is_file()], key=lambda p: str(p))

        if not self._ready:
            await self.startup()

        files_to_upload = self._prepare_files(files_to_upload)
        if not files_to_upload:
            logger.error("No files left to upload")
            return [UploadResponse(success=False, files=[])]

        album_id = None
        if album_name:
            album_id = await self._get_album_id(album_name)
            logger.debug(f"album id: '{album_id}'")

        async def worker(file_info: FileInfo, server: URL) -> UploadResponse:
            default_response = {"success": False, "files": [file_info.as_item]}
            async with self._max_connections:
                try:
                    return await self._upload_file(file_info, server=server)
                except FileUploadError as e:
                    logger.error(str(e), exc_info=True)
                return UploadResponse(**default_response)

        responses: list[UploadResponse] = []
        tasks: list = []
        for file_info in files_to_upload:
            default_response = {"success": False, "files": [file_info.as_item]}
            server = await self._get_server(album_id)
            if not server:
                responses.append(UploadResponse(**default_response))
                continue
            tasks.append(asyncio.create_task(worker(file_info, server)))

        uploads = await asyncio.gather(*tasks)
        responses.extend(uploads)
        return responses

    async def close(self) -> None:
        await self._api.close()
