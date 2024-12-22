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
        max_connections: int = 1,
        retries: int = 1,
        options: Optional[dict[str, Any]] = None,
    ):
        if options is None:
            options = {}
        self.options = options
        self.api = BunkrrAPI(token, max_connections, retries, options)
        self.temporary_files = []

    async def init(self):
        raw_req = await self.api.check()
        logger.debug(dict(raw_req))
        max_file_size = raw_req.get("maxSize", "0B")
        max_chunk_size = raw_req.get("chunkSize", {}).get("max", "0B")
        default_chunk_size = raw_req.get("chunkSize", {}).get("default", "0B")
        self.api._file_blacklist.extend(raw_req.get("stripTags", {}).get("blacklistExtensions", []))

        # Choose a chunk size, default or max
        chunk_size = default_chunk_size
        if self.options.get("use_max_chunk_size"):
            chunk_size = max_chunk_size

        if max_file_size == "0B" or chunk_size == "0B":
            raise ValueError("Invalid max file size or chunk size")

        # TODO: check if either one is 0 and abort

        units_to_calc = [max_file_size, chunk_size]
        units_calculated = []

        for unit in units_to_calc:
            size_str = unit.lower()
            unit_multiplier = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
            match = re.match(r"^(\d+)([a-z]+)$", size_str)

            if match:
                value, unit = match.groups()
                bytes_size = int(value) * unit_multiplier.get(unit, 1)
                units_calculated.append(bytes_size)
            else:
                raise ValueError("Invalid input format")

        self.api._max_file_size = units_calculated[0]
        self.api.chunk_size = units_calculated[1]

    def prepare_file_for_upload(self, file: Path) -> List[Path]:
        file_size = file.stat().st_size

        # TODO: Truncate the file name if it is too long
        file_name = (file.name[:240] + "..") if len(file.name) > 240 else file.name

        if file.suffix in self.api._file_blacklist:
            logger.error(f"File {file} has blacklisted extension {file.suffix}")
            return []

        if file_size > self.api._max_file_size:
            # TODO: Create temporary file archive
            logger.error(f"File {file} is bigger than max file size {self.api._max_file_size}")
            return []

        return [file]

    async def upload_files(self, path: Path, folder: Optional[str] = None) -> None:
        if path.is_file():
            paths = [path]
        else:
            logger.warning(f"only files at the root of the input folder will be uploaded (no recursion)")
            paths = sorted([x for x in path.iterdir() if x.is_file()], key=lambda p: str(p))
            if folder is None:
                folder = path.name

        if len(paths) == 0:
            logger.error("No file paths left to upload")
            return

        # The server may not accept certain file types and those over a certain size so we need to create temporary files
        filtered_paths = []
        for file_path in paths:
            filtered_paths.extend(self.prepare_file_for_upload(file_path))

        # TODO: Delete the extra created files after upload
        self.temporary_files = [x for x in filtered_paths if x not in paths]

        folder_id = None
        if folder:
            existing_folders = await self.api.get_albums()
            existing_folder = next((x for x in existing_folders["albums"] if x["name"] == folder), None)
            if existing_folder:
                folder_id = str(existing_folder["id"])
            else:
                logger.debug(f"album '{folder}' does not exists, creating")
                created_folder = await self.api.create_album(folder, folder)
                folder_id = str(created_folder["id"])
            logger.debug(f"album id: '{folder_id}'")

        if paths:
            responses = await self.api.upload_files(filtered_paths, folder_id)

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
                
    async def _upload_chunk(
        self,
        file: FileInfo,
        chunk: ChunkInfo
    ) -> bool:
        """
        Upload a single chunk with retry mechanism
        """
        dzchunkbyteoffset = self._chunk_size * chunk.index
        for attempt in range(self._chunk_retries):
            data = FormData()
            data.add_fields(
                ("dzuuid", file.uuid),
                ("dzchunkindex", str(chunk.index)),
                ("dztotalfilesize", str(file.size)),
                ("dzchunksize", str(self._chunk_size)),
                ("dztotalchunkcount", str(chunk.total)),
                ("dzchunkbyteoffset", str(dzchunkbyteoffset))
            )
            data.add_field(
                "files[]",
                chunk.data,
                filename=file.name,
                content_type="application/octet-stream",
            )
            try:
                async with self._session.post("/api/upload", data=data) as response:
                    response.raise_for_status()
                    if response.status == 200:
                        return True
                    
            except Exception as e:
                if attempt < self._chunk_retries - 1:
                    msg = f"{file.uuid} failed uploading chunk #{chunk.index}/{chunk.total} to {self.server} [{attempt+1}/{self._chunk_retries}]"
                    logger.error(msg)
                    await asyncio.sleep(self._retry_delay)
                    continue

                raise ChunkUploadError(chunk) from e
        
        return False
    
    async def _iter_chunks_read(self, file: FileInfo) -> AsyncIterator[ChunkInfo]:
        """
        Iterate over file chunks using read method
        """
        total_chunks = (file.size + self._chunk_size - 1) // self._chunk_size
        async with aiofiles.open(file.path, mode='rb') as file_data:
            index = 0
            while True:
                chunk_data = await file_data.read(self._chunk_size)
                if not chunk_data:
                    break
                yield ChunkInfo(chunk_data,index, total_chunks)
                index +=1

    async def upload_file(
        self, file_path: Path, album_id: str | None = None

    ) -> bool:
        """
        Upload a file in chunks with retry mechanism
        """
        file = FileInfo.from_path(file_path, album_id=album_id)
        
        for attempt in range(self._upload_retries):
            if file.size <= self._chunk_size:

            async with aiohttp.ClientSession() as session:
                try:
                    async for chunk in self._iter_chunks_read(file):
                        try:
                            await self._upload_chunk(file, chunk)
                            
                        except ChunkUploadError as e:
                            raise FileUploadError(file) from e
                        
                except Exception as e:
                    if attempt < self._chunk_retries - 1:
                        msg = f"{file.path} upload failed [{attempt+1}/{self._upload_retries}]"
                        logger.error(msg)
                        await asyncio.sleep(self._retry_delay)
                        continue

                    raise FileUploadError(file) from e
        print(f"Successfully uploaded {file.name}")
        return True
        


    # TODO: This should probably move out of API
    async def upload_file2(self, file_path: Path, album_id: str | None = None) -> UploadResponse:
        file = FileInfo.from_path(file_path, album_id=album_id)
        metadata = {
            "success": False,
            "files": [file.dump()]
        }

        node_response = await self.get_node()
        if not node_response.success:
            with tqdm.external_write_mode():
                logger.error(f"Failed to get server to upload to: {pformat(node_response)}")
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