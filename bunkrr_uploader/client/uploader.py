import asyncio
import hashlib
import logging
import mimetypes
import os
import uuid
from pathlib import Path
import aiofiles
from pprint import pformat
from typing import Any, BinaryIO, Optional

import asyncio
import csv
import logging
import re
import tempfile
import time
from pathlib import Path
from pprint import pformat, pprint
from typing import Any, List, Optional

from bunkrr_uploader.api import BunkrrAPI
from .cli import cli
from .logging_manager import USE_MAIN_NAME, setup_logger

from aiohttp import ClientSession, FormData
from tqdm import tqdm
from tqdm.asyncio import tqdm_asyncio
from yarl import URL
import aiohttp
import aiofiles
import os
import asyncio
from aiohttp import FormData
from typing import Optional, Dict, Any
from collections.abc import AsyncIterator
from datetime import timedelta

from pydantic import ByteSize

from bunkrr_uploader.api.types.responses import (
    AlbumsResponse,
    CheckResponse,
    CreateAlbumResponse,
    NodeResponse,
    UploadResponse,
    VerifyTokenResponse,
)

from bunkrr_uploader.api.types.files import  FileInfo, ChunkInfo
from bunkrr_uploader.util import ProgressFileReader, TqdmUpTo
from .errors import FileUploadError, ChunkUploadError

logger = logging.getLogger(__name__)


        


logger = logging.getLogger(__name__)

class BunkrrUploader:
    def __init__(
        self,
        token: str,
        *
        ,
        max_connections: int = 1,
        chunk_size: ByteSize | None = None,
        upload_retries: int = 1,
        chunk_retries: int = 2,
        upload_delay: float = 0.5,
        **kwargs: dict[str, Any]
    ):
        self.api = BunkrrAPI(token, chunk_size)
        assert max_connections <= self.api.RATE_LIMIT
        self._max_connections = max_connections
        self._upload_retries = upload_retries
        self._chunk_retries = chunk_retries
        self.options = kwargs
        self._upload_delay = upload_delay
        self.temporary_files = []
        self._ready = False

    async def startup(self):
        await self.api.startup()
        self._chunk_size = self.api._chunk_size
        self._ready = True

    def prepare_files(self, files: list[Path]) -> list[FileInfo]:
        files_to_upload = []
        for file in files:
            file_info = FileInfo(file)
            if file_info.path.suffix.casefold() in self.api.info.stripTags.blacklistExtensions:
                logger.error(f"File {file} has blacklisted extension {file.suffix}")

            elif file_info.size > self.api.info.maxSize:
                logger.error(f"File {file} is bigger than max file size {self.api.info.maxSize.human_readable(decimal=True)}")
            else:
                files_to_upload.append(file_info)

        return files_to_upload

    async def upload_files2(self, path: Path, recurse: bool = False, album_name: str | None = None) -> None:
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
        files_to_upload = self.prepare_files(files_to_upload)
        if not files_to_upload:
            logger.error("No files left to upload")
            return

        folder_id = None
        if album_name:
            existing_albums = await self.api.get_albums()
            album = next((x for x in existing_albums.albums if x.name == album_name), None)
            if not album:
                logger.debug(f"album '{album_name}' does not exists, creating")
                album = await self.api.create_album(album_name, description=album_name)
            folder_id = album.id
            logger.debug(f"album id: '{folder_id}'")

        responses = await self.api.upload_files(files_to_upload, folder_id)

        if self.options.get("save") and responses:
            expected_fieldnames = ["albumid", "filePathMD5", "fileNameMD5", "filePath", "fileName", "uploadSuccess"]
            response_fields = list(
                set(expected_fieldnames + list(set().union(*[x.keys() for x in responses[0]["files"] if x])))
            )

            file_name = f"bunkrr_upload_{int(time.time())}.csv"
            with open(file_name, "w", newline="") as csvfile:
                logger.info(f"Saving uploaded files to {file_name}")
                csv_writer = csv.DictWriter(csvfile, dialect="excel", fieldnames=response_fields)
                csv_writer.writeheader()
                for row in responses:
                    csv_writer.writerow(row["files"][0])

        else:
            pprint(responses)
                
    async def _upload_chunk(self, file_info: FileInfo, chunk: ChunkInfo) -> bool:
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
                ("dzchunkbyteoffset", str(dzchunkbyteoffset))
            )
            data.add_field(
                "files[]",
                chunk.data,
                filename=file_info.upload_name,
                content_type="application/octet-stream",
            )
            try:
                response = await self.api._post("/upload", data=data)
                response = UploadResponse(**response)
                return response.success

            except Exception as e:
                if attempt < self._chunk_retries - 1:
                    msg = f"{file_info.uuid} failed uploading chunk #{chunk.index}/{chunk.total} to {self.server} [{attempt+1}/{self._chunk_retries}]"
                    logger.error(msg)
                    await asyncio.sleep(self._upload_delay)
                    continue

                raise FileUploadError(file_info) from e
        
        return False
    
    async def _iter_chunks_read(self, file: FileInfo) -> AsyncIterator[ChunkInfo]:
        """Iterate over file chunks using read method."""
        total_chunks = (file.size + self._chunk_size - 1) // self._chunk_size
        async with aiofiles.open(file.path, mode='rb') as file_data:
            index = 0
            while True:
                chunk_data = await file_data.read(self._chunk_size)
                if not chunk_data:
                    break
                yield ChunkInfo(chunk_data,index, total_chunks)
                index +=1

    async def upload_file(self, file_path: Path, album_id: str | None = None) -> bool:
        """Upload a file in chunks with retry mechanism."""
        file_info = FileInfo(file_path, album_id=album_id)
        for attempt in range(self._upload_retries):
            try:
                if file_info.size <= self._chunk_size:
                    await self.api.upload(file_info)
                else:
                    async for chunk in self._iter_chunks_read(file_info):
                        await self._upload_chunk(file_info, chunk)

            except Exception as e:
                if attempt < self._upload_retries - 1:
                    msg = f"{file_info.path} upload failed [{attempt+1}/{self._upload_retries}]"
                    logger.error(msg)
                    await asyncio.sleep(self._upload_delay)
                    continue

                raise FileUploadError(file_info) from e

        print(f"Successfully uploaded {file_info.original_name}")
        return True



    # TODO: This should probably move out of API
    async def upload_file2(self, file_path: Path, album_id: str | None = None) -> UploadResponse:
        file = FileInfo.from_path(file_path, album_id=album_id)
        metadata = {
            "success": False,
            "files": [file.dump()]
        }

        node_response = await self.api.get_node()
        if not node_response.success:
            return UploadResponse(**metadata)

        server = "/".join(node_response.url.split("/")[:3])

        if server not in self.server_sessions:
            with tqdm.external_write_mode():
                logger.info(f"Using new server connection to {server}")
            self.server_sessions[server] = ClientSession(server, headers=self._session_headers)

        session = self.server_sessions[server]

        headers = {"albumid": album_id} if album_id else None

        async with self._semaphore:
            retries = 0
            while retries < self._upload_retries:
                try:
                    with open(file_path, "rb") as file_data:
                            with ProgressFileReader(filename=file_path, read_callback=t.update_to) as file_data:
                                
                                else:
                                    with tqdm.external_write_mode():
                                        logger.debug(f"{file_path.name} will use UUID {file.uuid}")
                                    await self.upload_chunks(
                                        file_data, file, server
                                    )

                                    upload_data = {
                                        "files": [
                                            {
                                                "uuid": file.uuid,
                                                "original": file.name,
                                                "type": file.mimetype,
                                                "albumid": album_id or "",
                                                "filelength": "",
                                                "age": "",
                                            }
                                        ]
                                    }
                                    finish_chunks_attempt = 0
                                    while True:
                                        try:
                                            async with session.post(
                                                "/api/upload/finishchunks", json=upload_data
                                            ) as resp:
                                                response = await resp.json()
                                                if response.get("success") is False:
                                                    msg = f"{file.uuid} failed finishing chunks to {server} [{finish_chunks_attempt + 1}/{self._chunk_retries}]\n{pformat(response)}"
                                                    with tqdm.external_write_mode():
                                                        logger.error(msg)
                                                    raise Exception(msg)
                                                # chunk_upload_success = True
                                                response.update(metadata)
                                                return response
                                        except Exception:
                                            finish_chunks_attempt += 1
                                            if finish_chunks_attempt >= self._chunk_retries:
                                                raise
                                    # TODO: Should probably return here
                except Exception:
                    with tqdm.external_write_mode():
                        logger.exception(f"Upload failed for {file_path.name} to {server} Attempt #{retries + 1}")
                    retries += 1
            return {"success": False, "files": [{"name": file_path.name, "url": ""}]}

    # TODO: This should probably move out of API
    async def upload_files(self, paths: list[Path], folder_id: Optional[str] = None) -> list[UploadResponse]:

        try:
            tasks = [self.upload(test_file, folder_id) for i, test_file in enumerate(paths)]
            responses = await tqdm_asyncio.gather(*tasks, desc="Files uploaded", position=0, leave=False)
            return responses
        finally:
            # This should happen in the API client itself
            await self._session.close()
            for server_session in self.server_sessions.values():
                await server_session.close()