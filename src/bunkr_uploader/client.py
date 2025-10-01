from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Self

import aiofiles
from aiohttp import FormData
from pydantic import ByteSize

from bunkr_uploader.api import BunkrrAPI
from bunkr_uploader.api._exceptions import FileUploadError
from bunkr_uploader.api._files import Chunk, File
from bunkr_uploader.api._responses import Upload, UploadItemResponse

from .progress import new_progress

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator, Sequence
    from pathlib import Path

    from yarl import URL

    from bunkr_uploader.config import ConfigSettings

logger = logging.getLogger(__name__)


class BunkrrUploader:
    def __init__(self, settings: ConfigSettings) -> None:
        self.settings = settings
        self._api = BunkrrAPI(settings.token, settings.chunk_size)
        assert self.settings.concurrent_uploads <= self._api.RATE_LIMIT
        self._sem = asyncio.BoundedSemaphore(settings.concurrent_uploads)
        self._ready = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def startup(self) -> None:
        await self._api.startup()
        if self.settings.use_max_chunk_size:
            self._api._chunk_size = self._api._info.chunkSize.max
        else:
            self._api._chunk_size = self.settings.chunk_size or ByteSize(10 * 1024 * 1024)

        self._chunk_size = self._api._chunk_size
        self._progress = new_progress()
        self._ready = True

    def _prepare_files(self, files: Sequence[Path]) -> Generator[File]:
        max_size = self._api._info.maxSize.human_readable(decimal=True)
        for path in files:
            file = File.from_path(path)
            if path.suffix.casefold() in self._api._info.stripTags["blacklistExtensions"]:
                logger.error(f"File {path} has blacklisted extension {path.suffix}")

            elif file.size > self._api._info.maxSize:
                msg = f"File '{path}' ({file.size:,}) is bigger than max file size: {self._api._info.maxSize:,} ({max_size})"
                logger.error(msg)

            else:
                yield file

    async def _upload_chunk(
        self, file: File, chunk: Chunk, server: URL, album_id: str | None
    ) -> bool:
        """Upload a single chunk with retry mechanism."""
        for attempt in range(self.settings.chunk_retries):
            form = self._create_chunk_dataform(file, chunk, album_id)
            try:
                resp = await self._api._request("upload", data=form)
                resp = Upload(**resp)
                return resp.success

            except Exception as e:
                if attempt < self.settings.chunk_retries:
                    msg = f"{file.uuid} failed uploading chunk #{chunk.index + 1}/{chunk.total} to {server} [{attempt + 1}/{self.settings.chunk_retries}]"
                    logger.error(msg, exc_info=True)
                    await asyncio.sleep(self.settings.upload_delay)
                    continue
                raise FileUploadError(file) from e

        return False

    def _create_chunk_dataform(
        self, file_info: File, chunk: Chunk, album_id: str | None
    ) -> FormData:
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
        if album_id:
            form.add_field("albumid", str(album_id))
        return form

    async def _chunked_read(self, file_info: File) -> AsyncIterator[Chunk]:
        """Iterate over file chunks."""
        total_chunks = (file_info.size + self._chunk_size - 1) // self._chunk_size
        index = 0
        task_id = self._progress.add_task(file_info.original_name, total=file_info.size)
        try:
            async with aiofiles.open(file_info.path, mode="rb") as file_data:
                while chunk_data := await file_data.read(self._chunk_size):
                    chunk_offset = self._chunk_size * index
                    mem_view = memoryview(chunk_data)
                    yield Chunk(mem_view, index, total_chunks, chunk_offset)
                    self._progress.advance(task_id, len(mem_view))
                    index += 1
        finally:
            self._progress.remove_task(task_id)

    async def _upload_file(self, file: File, server: URL, album_id: str | None) -> Upload:
        """Upload a file in chunks with retry mechanism."""
        finish_chunks = False
        for attempt in range(self.settings.upload_retries):
            try:
                if file.size <= self._chunk_size:
                    return await self._api.direct_upload(file, server, album_id)

                if not finish_chunks:
                    async for chunk in self._chunked_read(file):
                        await self._upload_chunk(file, chunk, server, album_id)
                    finish_chunks = True

                if finish_chunks:
                    return await self._api.finish_chunks(file, server)

            except Exception as e:
                if attempt < self.settings.upload_retries - 1:
                    msg = (
                        f"{file.path} upload failed [{attempt + 1}/{self.settings.upload_retries}]"
                    )
                    logger.error(msg)
                    await asyncio.sleep(self.settings.upload_delay)
                    continue

                raise FileUploadError(file) from e

        item = UploadItemResponse(name=file.path.name, url=None, success=False)
        return Upload(success=False, files=[item])

    async def _get_server(self) -> URL:
        node_response = await self._api.get_node()
        if not node_response.success:
            raise RuntimeError("Unable to get server to upload")

        server = node_response.url.with_path("/api/")
        logger.info(f"{server = }")
        return server

    async def _get_album_id(self, album_name: str) -> int:
        existing_albums = await self._api.get_albums()
        album = next((x for x in existing_albums.albums if x.name == album_name), None)
        if not album:
            msg = f"album '{album_name}' does not exists, creating"
            logger.debug(msg)
            album = await self._api.create_album(album_name, description=album_name)
        return album.id

    async def _get_files_to_upload(self, path: Path, recurse: bool) -> list[File]:
        if not path.exists():
            raise FileNotFoundError
        if path.is_file():
            files_to_upload = [path]

        elif recurse:
            files_to_upload = path.rglob("*")
        else:
            files_to_upload = path.iterdir()

        files_to_upload = sorted(
            (x for x in files_to_upload if x.is_file()), key=lambda p: str(p).casefold()
        )

        if not self._ready:
            await self.startup()

        return list(self._prepare_files(files_to_upload))

    async def upload(
        self, path: Path, recurse: bool = False, *, album_name: str | None = None
    ) -> list[tuple[File, Upload]]:
        files_to_upload = await self._get_files_to_upload(path, recurse)
        if not files_to_upload:
            logger.error("No files left to upload")
            return []

        album_id = None
        if album_name:
            album_id = str(await self._get_album_id(album_name))
            logger.debug(f"album id: '{album_id}'")

        tasks = []

        async with asyncio.TaskGroup() as tg:
            with self._progress:
                for file_info in files_to_upload:
                    await self._sem.acquire()
                    tasks.append(
                        tg.create_task(self._try_upload(file_info, album_id)),
                    )

        responses: list[tuple[File, Upload]] = await asyncio.gather(*tasks)
        return responses

    async def _try_upload(self, file: File, album_id: str | None) -> tuple[File, Upload] | None:
        try:
            server = await self._get_server()
            response = await self._upload_file(file, server, album_id)
            return file, response
        except Exception as e:
            logger.error(str(e), exc_info=True)
        finally:
            self._sem.release()

        failed_resp = Upload(success=False, files=[file.as_item()])
        return file, failed_resp

    async def close(self) -> None:
        await self._api.close()
