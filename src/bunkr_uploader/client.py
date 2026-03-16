from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Self

import aiofiles
from pydantic import TypeAdapter

from bunkr_uploader.api import BunkrAPI
from bunkr_uploader.api.errors import ChunkUploadError, FileUploadError
from bunkr_uploader.api.file import Chunk, FileUpload
from bunkr_uploader.api.responses import Info, UploadResponse
from bunkr_uploader.logger import utc_now
from bunkr_uploader.progress import new_progress

if TYPE_CHECKING:
    import datetime
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable
    from pathlib import Path

    from rich.progress import Progress
    from yarl import URL

    from bunkr_uploader.config import Config


logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class FileUploadResult:
    file: FileUpload
    result: UploadResponse
    timestamp: datetime.datetime = dataclasses.field(init=False, default_factory=utc_now)

    def __str__(self) -> str:
        return _file_upload_result_serializer(self).decode()


_file_upload_result_serializer = TypeAdapter(FileUploadResult).dump_json


@dataclasses.dataclass(slots=True)
class BunkrUploader:
    config: Config
    upload_callback: Callable[[FileUploadResult], None] = lambda _: None

    _api: BunkrAPI = dataclasses.field(init=False)
    _sem: asyncio.BoundedSemaphore = dataclasses.field(init=False)
    _progress: Progress = dataclasses.field(init=False, default_factory=new_progress)

    def __post_init__(self) -> None:
        self._api = BunkrAPI(self.config.token, self.config.chunk_size or 0)
        self._sem = asyncio.BoundedSemaphore(self.config.concurrent_uploads)

    async def __aenter__(self) -> Self:
        await self._api.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._api.close()

    async def _upload_chunk(self, file: FileUpload, server: URL, chunk: Chunk) -> bool:
        """Upload a single chunk with retry mechanism."""
        for attempt in range(self.config.chunk_retries):
            msg = (
                f"uploading chunk {chunk.index} of file '{file.original_name}'"
                f" (attempt {attempt + 1}/{self.config.chunk_retries})"
            )
            logger.info(msg)
            try:
                await self._api.upload_chunk(file, server, chunk)
                return True

            except ChunkUploadError as e:
                if attempt < self.config.chunk_retries:
                    logger.error(str(e))
                    await asyncio.sleep(self.config.delay)
                    continue
                raise FileUploadError(file) from e

        return False

    async def _chunked_read(self, file: FileUpload) -> AsyncIterator[Chunk]:
        """Iterate over file chunks."""
        n_chunks = (file.size + self._api.chunk_size - 1) // self._api.chunk_size
        index = 0
        task_id = self._progress.add_task(file.original_name, total=file.size)
        try:
            async with aiofiles.open(file.path, mode="rb") as file_data:
                while data := await file_data.read(self._api.chunk_size):
                    offset = self._api.chunk_size * index
                    mem_view = memoryview(data)
                    yield Chunk(mem_view, index, n_chunks, offset)
                    self._progress.advance(task_id, len(mem_view))
                    index += 1
        finally:
            self._progress.remove_task(task_id)

    async def _upload_file(self, file: FileUpload, server: URL) -> UploadResponse:
        """Upload a file in chunks with retry mechanism."""
        info = await self._api.check()
        for attempt in range(self.config.retries):
            try:
                if file.size <= info.maxSize:
                    return await self._api.direct_upload(file, server)

                async for chunk in self._chunked_read(file):
                    _ = await self._upload_chunk(file, server, chunk)

                return await self._api.finish_chunks(file, server)

            except FileUploadError as e:
                if attempt < self.config.retries - 1:
                    msg = f"{e} (attempt {attempt + 1}/{self.config.retries})"
                    logger.error(msg)
                    await asyncio.sleep(self.config.delay)
                    continue

                msg = (
                    f"Skipping upload of '{file.path}' after {self.config.retries} failed attempts"
                )
                logger.exception(msg)

        failed_file_resp = file.as_response()
        return UploadResponse(success=False, files=[failed_file_resp])

    async def _request_upload_server(self) -> URL:
        node_response = await self._api.node()
        if not node_response.success:
            raise RuntimeError("Unable to get server to upload")

        return node_response.url.with_path("/api/")

    async def _get_album_id(self, album_name: str) -> int:
        existing_albums = await self._api.get_albums()
        album = next((x for x in existing_albums.albums if x.name == album_name), None)
        if not album:
            logger.info(f"album '{album_name}' does not exists, creating")
            album = await self._api.create_album(album_name, description=album_name)
        return album.id

    async def _get_files_to_upload(self, path: Path, recurse: bool) -> list[FileUpload]:
        if not path.exists():
            raise FileNotFoundError(path)
        if path.is_file():
            files_to_upload = [path]

        elif recurse:
            files_to_upload = path.rglob("*")
        else:
            files_to_upload = path.iterdir()

        files_to_upload = (f for f in files_to_upload if f.is_file())

        info = await self._api.check()
        return sorted(
            [file async for file in _prepare_files(files_to_upload, info)],
            key=lambda p: str(p.path).casefold(),
        )

    async def upload(
        self, path: Path, recurse: bool = False, *, album_name: str | None = None
    ) -> list[FileUploadResult]:
        files_to_upload = await self._get_files_to_upload(path, recurse)
        if not files_to_upload:
            logger.error("No files left to upload")
            return []

        album_id = None
        album_name = album_name or self.config.album_name
        if album_name:
            album_id = str(await self._get_album_id(album_name))
            logger.debug(f"album id: '{album_id}'")

        tasks: list[asyncio.Task[FileUploadResult | None]] = []

        async with asyncio.TaskGroup() as tg:
            with self._progress:
                for file in files_to_upload:
                    _ = await self._sem.acquire()
                    tasks.append(
                        tg.create_task(self._try_upload(file, album_id)),
                    )

        return [result for t in tasks if (result := t.result()) if not None]

    async def _try_upload(self, file: FileUpload, album_id: str | None) -> FileUploadResult | None:
        file.album_id = album_id
        try:
            server = await self._request_upload_server()
            logger.info(f"Using {server = } for upload of '{file.path}'")
            response = await self._upload_file(file, server)
        except Exception:
            logger.exception(f"Upload of '{file.path}' failed")
        else:
            result = FileUploadResult(file, response)
            self.upload_callback(result)
            return result
        finally:
            self._sem.release()


async def _prepare_files(files: Iterable[Path], info: Info) -> AsyncGenerator[FileUpload]:
    human_max_size = info.maxSize.human_readable(decimal=True)

    async def prepare(path: Path) -> FileUpload | None:
        try:
            file = await asyncio.to_thread(FileUpload.from_path, path)
        except Exception:
            logger.exception(f"Unable to prepare file '{path}'")
            return

        if path.suffix.casefold() in info.stripTags.blacklistExtensions:
            logger.error(f"File '{path}' has blacklisted extension: {path.suffix}")
            return

        if file.size > info.maxSize:
            msg = f"File '{path}' ({file.size:,}) is bigger than max file size: {info.maxSize:,} ({human_max_size})"
            logger.error(msg)
            return

        return file

    for fut in asyncio.as_completed(prepare(path) for path in files):
        if file := await fut:
            yield file
