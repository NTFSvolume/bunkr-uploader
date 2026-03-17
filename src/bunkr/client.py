from __future__ import annotations

import asyncio
import dataclasses
import datetime  # noqa: TC003
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import TypeAdapter

from bunkr import aio, progress
from bunkr.api import BunkrAPI
from bunkr.api.errors import ChunkUploadError, FileUploadError
from bunkr.api.responses import UploadResponse
from bunkr.api.upload import Chunk, FileUpload
from bunkr.logger import utc_now

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from yarl import URL

    from bunkr.config import Config


logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class FileUploadResult:
    file: FileUpload
    result: UploadResponse
    timestamp: datetime.datetime = dataclasses.field(init=False, default_factory=utc_now)

    _serializer: ClassVar[Callable[[Self], bytes] | None] = None

    @classmethod
    def __new__(cls) -> Self:
        if cls._serializer is None:
            cls._serializer = TypeAdapter(cls).dump_json
        return super(FileUploadResult, cls).__new__(cls)

    def __str__(self) -> str:
        assert self._serializer is not None
        return self._serializer(self).decode()


@dataclasses.dataclass(slots=True)
class BunkrUploader:
    config: Config
    upload_callback: Callable[[FileUploadResult], None] = lambda _: None

    _api: BunkrAPI = dataclasses.field(init=False)
    _sem: asyncio.BoundedSemaphore = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self._api = BunkrAPI(self.config.token, self.config.chunk_size or 0)
        self._sem = asyncio.BoundedSemaphore(self.config.concurrent_uploads)

    async def __aenter__(self) -> Self:
        _ = await self._api.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._api.__aexit__(*args)

    async def _direct_upload(self, upload: FileUpload, server: URL) -> UploadResponse:
        with progress.new_upload(upload.original_name, upload.size) as hook:
            result = await self._api.upload(upload, server)
            hook.advance(upload.size)
            return result

    async def _chunked_upload(self, upload: FileUpload, server: URL) -> UploadResponse:
        with progress.new_upload(upload.original_name, upload.size) as hook:
            async for chunk in _iter_chunked(upload, self._api.chunk_size):
                _ = await self._upload_chunk(upload, server, chunk)
                hook.advance(len(chunk.data))

            return await self._api.finish_chunks(upload, server)

    async def _upload_chunk(self, upload: FileUpload, server: URL, chunk: Chunk) -> None:
        """Upload a single chunk with retry mechanism."""
        for attempt in range(self.config.chunk_retries):
            msg = (
                f"Uploading chunk {chunk.index + 1} of file '{upload.original_name}'"
                f" (attempt {attempt + 1}/{self.config.chunk_retries})"
            )
            logger.info(msg)
            try:
                await self._api.upload_chunk(upload, server, chunk)

            except ChunkUploadError as e:
                if attempt < self.config.chunk_retries - 1:
                    logger.error(str(e))
                    await asyncio.sleep(self.config.delay)
                    continue
                raise FileUploadError(upload) from e.__cause__

    async def _upload(self, upload: FileUpload, server: URL) -> UploadResponse:
        """Upload a file in chunks with retry mechanism."""
        info = await self._api.check()
        for attempt in range(self.config.retries):
            try:
                if upload.size <= info.chunkSize.max:
                    return await self._direct_upload(upload, server)

                else:
                    return await self._chunked_upload(upload, server)

            except FileUploadError as e:
                cause = e.__cause__ or e
                if attempt < self.config.retries - 1:
                    msg = f"{cause} (attempt {attempt + 1}/{self.config.retries})"
                    logger.error(msg)
                    await asyncio.sleep(self.config.delay)
                    continue

                msg = f"Skipping upload of '{upload.path}' after {self.config.retries} failed attempt(s) ({str(cause)[:40]}"
                logger.error(msg, exc_info=cause)

        return UploadResponse(success=False, files=[upload.as_failed_resp()])

    async def _request_upload_server(self) -> URL:
        node_response = await self._api.node()
        if not node_response.success:
            raise RuntimeError("Unable to get server to upload")

        return node_response.url.with_path("/api/")

    async def _get_album_id(self, album_name: str) -> int:
        albums = await self._api.get_albums()
        album = next((album for album in albums if album.name == album_name), None)
        if not album:
            logger.info(f"Album '{album_name}' does not exists, creating")
            album = await self._api.create_album(album_name, description=album_name)
        return album.id

    async def _prepare_uploads(self, path: Path, *, recurse: bool) -> tuple[FileUpload, ...]:
        files = await asyncio.to_thread(_get_files, path, recurse=recurse)
        info = await self._api.check()
        human_max_size = info.maxSize.human_readable(decimal=True)

        async def _prepare_upload(path: Path) -> FileUpload | None:
            try:
                if path.suffix.casefold() in info.stripTags.blacklistExtensions:
                    logger.error(f"File '{path}' has blacklisted extension: {path.suffix}")
                    return

                size = (await asyncio.to_thread(path.stat)).st_size

                if size > info.maxSize:
                    msg = f"File '{path}' ({size:,}) is bigger than max file size: {info.maxSize:,} ({human_max_size})"
                    logger.error(msg)
                    return

                upload = await asyncio.to_thread(FileUpload.create, path, size)
            except Exception:
                logger.exception(f"Unable to prepare upload of '{path}'")
                return

            else:
                return upload

        return tuple(filter(None, await asyncio.gather(*map(_prepare_upload, files))))

    async def upload(
        self, path: Path, *, recurse: bool = False, album: str | None = None
    ) -> list[FileUploadResult]:
        files_to_upload = await self._prepare_uploads(path, recurse=recurse)
        if not files_to_upload:
            logger.error("No files left to upload")
            return []

        album_id = None
        album = album or self.config.album
        if album:
            album_id = str(await self._get_album_id(album))
            logger.debug(f"album id: '{album_id}'")

        tasks: list[asyncio.Task[FileUploadResult | None]] = []

        with progress.new_progress():
            async with asyncio.TaskGroup() as tg:
                for file in files_to_upload:
                    _ = await self._sem.acquire()
                    tasks.append(
                        tg.create_task(self._try_upload(file, album_id)),
                    )

        return [result for t in tasks if (result := t.result()) if not None]

    async def _try_upload(
        self, upload: FileUpload, album_id: str | None
    ) -> FileUploadResult | None:
        upload.album_id = album_id
        try:
            server = await self._request_upload_server()
            logger.info(f"Using {server = !s} for upload of '{upload.path}'")
            response = await self._upload(upload, server)
        except Exception:
            logger.exception(f"Upload of '{upload.path}' failed")
        else:
            result = FileUploadResult(upload, response)
            self.upload_callback(result)
            return result
        finally:
            self._sem.release()


def _get_files(path: Path, *, recurse: bool) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_file():
        files_to_upload = [path]

    elif recurse:
        files_to_upload = path.rglob("*")
    else:
        files_to_upload = path.iterdir()

    return sorted(
        (f for f in files_to_upload if f.is_file()), key=lambda p: p.as_posix().casefold()
    )


async def _iter_chunked(upload: FileUpload, chunk_size: int) -> AsyncIterator[Chunk]:
    """Iterate over file chunks."""
    n_chunks = (upload.size + chunk_size - 1) // chunk_size
    index = 0
    async with aio.open(upload.path, mode="rb") as fp:
        while data := await fp.read(chunk_size):
            offset = chunk_size * index
            mem_view = memoryview(data)
            yield Chunk(mem_view, index, n_chunks, offset)
            index += 1
