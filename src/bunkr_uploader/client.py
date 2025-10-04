from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Self

import aiofiles
from pydantic import ByteSize, TypeAdapter

from bunkr_uploader.api import BunkrrAPI
from bunkr_uploader.api._exceptions import ChunkUploadError, FileUploadError
from bunkr_uploader.api._files import Chunk, File
from bunkr_uploader.api._responses import UploadResponse

from .logger import utc_now, write_to_jsonl
from .progress import new_progress

if TYPE_CHECKING:
    import datetime
    from collections.abc import AsyncIterator, Generator, Iterable
    from pathlib import Path

    from yarl import URL

    from bunkr_uploader.config import ConfigSettings


logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class FileUploadResult:
    file: File
    result: UploadResponse
    timestamp: datetime.datetime = dataclasses.field(init=False, default_factory=utc_now)

    def dumps(self) -> str:
        return _file_upload_result_serializer(self).decode()


_file_upload_result_serializer = TypeAdapter(FileUploadResult).dump_json


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

    def _prepare_files(self, files: Iterable[Path]) -> Generator[File]:
        max_size = self._api._info.maxSize.human_readable(decimal=True)
        for path in files:
            try:
                file = File.from_path(path)
            except Exception:
                logger.exception(f"Unable to prepare file '{path}'")
                continue

            if path.suffix.casefold() in self._api._info.stripTags["blacklistExtensions"]:
                logger.error(f"File {path} has blacklisted extension {path.suffix}")

            elif file.size > self._api._info.maxSize:
                msg = f"File '{path}' ({file.size:,}) is bigger than max file size: {self._api._info.maxSize:,} ({max_size})"
                logger.error(msg)

            else:
                yield file

    async def _upload_chunk(self, file: File, server: URL, chunk: Chunk) -> bool:
        """Upload a single chunk with retry mechanism."""
        for attempt in range(self.settings.chunk_retries):
            msg = f"uploading chunk {chunk.index} of file {file.original_name} (attempt {attempt + 1}/{self.settings.chunk_retries})"
            logger.info(msg)
            try:
                await self._api.upload_chunk(file, server, chunk)
                return True

            except ChunkUploadError as e:
                if attempt < self.settings.chunk_retries:
                    logger.error(str(e))
                    await asyncio.sleep(self.settings.upload_delay)
                    continue
                raise FileUploadError(file) from e

        return False

    async def _chunked_read(self, file: File) -> AsyncIterator[Chunk]:
        """Iterate over file chunks."""
        total_chunks = (file.size + self._chunk_size - 1) // self._chunk_size
        index = 0
        task_id = self._progress.add_task(file.original_name, total=file.size)
        try:
            async with aiofiles.open(file.path, mode="rb") as file_data:
                while chunk_data := await file_data.read(self._chunk_size):
                    chunk_offset = self._chunk_size * index
                    mem_view = memoryview(chunk_data)
                    yield Chunk(mem_view, index, total_chunks, chunk_offset)
                    self._progress.advance(task_id, len(mem_view))
                    index += 1
        finally:
            self._progress.remove_task(task_id)

    async def _upload_file(self, file: File, server: URL) -> UploadResponse:
        """Upload a file in chunks with retry mechanism."""
        for attempt in range(self.settings.upload_retries):
            try:
                if file.size <= self._chunk_size:
                    return await self._api.direct_upload(file, server)

                async for chunk in self._chunked_read(file):
                    await self._upload_chunk(file, server, chunk)

                return await self._api.finish_chunks(file, server)

            except FileUploadError as e:
                if attempt < self.settings.upload_retries - 1:
                    msg = f"{e} (attempt {attempt + 1}/{self.settings.upload_retries})"
                    logger.error(msg)
                    await asyncio.sleep(self.settings.upload_delay)
                    continue

                msg = f"Skipping upload of '{file.path}' after {self.settings.upload_retries} failed attempts"
                logger.exception(msg)

        failed_file_resp = file.as_response()
        return UploadResponse(success=False, files=[failed_file_resp])

    async def _get_server(self) -> URL:
        node_response = await self._api.node()
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
            logger.info(msg)
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

        files_to_upload = (f for f in files_to_upload if f.is_file())

        if not self._ready:
            await self.startup()

        return sorted(self._prepare_files(files_to_upload), key=lambda p: str(p.path).casefold())

    async def upload(
        self, path: Path, recurse: bool = False, *, album_name: str | None = None
    ) -> list[FileUploadResult]:
        files_to_upload = await self._get_files_to_upload(path, recurse)
        if not files_to_upload:
            logger.error("No files left to upload")
            return []

        album_id = None
        if album_name:
            album_id = str(await self._get_album_id(album_name))
            logger.debug(f"album id: '{album_id}'")

        tasks: list[asyncio.Task[FileUploadResult | None]] = []

        async with asyncio.TaskGroup() as tg:
            with self._progress:
                for file in files_to_upload:
                    await self._sem.acquire()
                    tasks.append(
                        tg.create_task(self._try_upload(file, album_id)),
                    )

        return list(filter(None, await asyncio.gather(*tasks)))

    async def _try_upload(self, file: File, album_id: str | None) -> FileUploadResult | None:
        file.album_id = album_id
        try:
            server = await self._get_server()
            response = await self._upload_file(file, server)
            result = FileUploadResult(file, response)
            write_to_jsonl(result)
            return result
        except Exception:
            logger.exception(f"Upload of '{file.path}' failed")
        finally:
            self._sem.release()

    async def close(self) -> None:
        await self._api.close()
