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

from bunkrr_uploader.api.types.custom_types import  BunkrrFile, Chunk
from bunkrr_uploader.util import ProgressFileReader, TqdmUpTo

logger = logging.getLogger(__name__)


class ChunkUploadError(Exception):
    """Custom exception for chunk upload failures"""
    def __init__(self, chunk: Chunk) -> None:
        self.chunk = chunk
        range = chunk.byte_range
        self.message = f"Failed to upload chunk #{self.chunk.index} - Range: b{range[0]}-{range[1]}"
        super().__init__(self.message)

class FileUploadError(Exception):
    """Custom exception for file upload failures"""
    def __init__(self, file: BunkrrFile) -> None:
        self.file = file
        self.message = f"Failed to upload {self.file.file_path}"
        super().__init__(self.message)

class BunkrrAPI:
    def __init__(
        self,
        token: str,
        max_connections: int = 2,
        retries: int = 2,
        options: dict[str, str] | None = None,
    ):
        if options is None:
            options = {}

        self._token = token
        self.url = URL("https://app.bunkrr.su/")
        self.download_url_base = URL("https://bunkrr.ru/d/")

        self.max_file_size = 1800
        self._chunk_size = 20
        self.file_blacklist = []

        self.options = options

        self._session_headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
            "token": self._token,
        }
        self._session = ClientSession(self.url, headers=self._session_headers)

        self.server_sessions = {}
        self.created_folders = {}
        self._semaphore = asyncio.Semaphore(max_connections)
        self._upload_retries = retries
        self._chunk_retries: int = options.get("chunk_retries", 1)
        self._retry_delay = 0.5

    async def _get_json(self, path: str) -> dict:
        async with self._session.get(path) as resp:
            response = await resp.json()
            return response

    async def _post(self, path: str, *, data: FormData | dict | None = None) -> dict:
        data = data or {}
        if isinstance(data, dict):
            data["token"] = data.get("token", self._token)
        async with self._session.post(path, data=data) as resp:
            response = await resp.json()
            return response
        
    """----------------------------------------------------------------------------------------------"""

    async def get_check(self) -> CheckResponse:
        response = await self._get_json("/api/check")
        return CheckResponse(**response)

    async def get_node(self) -> NodeResponse:
        response = await self._get_json("/api/node")
        return NodeResponse(**response)

    async def verify_token(self) -> VerifyTokenResponse:
        response = await self._post("/api/tokens/verify")
        return VerifyTokenResponse(**response)

    async def get_albums(self) -> AlbumsResponse:
        response = await self._get_json("/api/albums")
        return AlbumsResponse(**response)

    async def create_album(
        self, name: str, *, description: str = "", public: bool = True, download: bool = True
    ) -> CreateAlbumResponse:
        data = {"name": name, "description": description, "public": public, "download": download}
        response = await self._post("/api/albums", data=data)
        return CreateAlbumResponse(**response)

    async def _upload_chunk(
        self,
        file: BunkrrFile,
        chunk: Chunk
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
    
    async def _iter_chunks_read(self, file: BunkrrFile) -> AsyncIterator[Chunk]:
        """
        Iterate over file chunks using read method
        """
        total_chunks = (file.size + self._chunk_size - 1) // self._chunk_size
        async with aiofiles.open(file.file_path, mode='rb') as file_data:
            index = 0
            while True:
                chunk_data = await file_data.read(self._chunk_size)
                if not chunk_data:
                    break
                yield Chunk(chunk_data,index, total_chunks)
                index +=1

    async def _simple_upload(self, file: BunkrrFile, session: Any):
        chunk_data = file.data.read(self._chunk_size)
        data = FormData()
        data.add_field(
            "files[]", chunk_data, filename=file.name, content_type=file.mimetype
        )

        async with session.post("/api/upload", data=data) as resp:
            response = await resp.json()
            if not response.get("success"):
                raise Exception(f"{file.name} failed uploading without chunks")

            return response

    async def upload_file(
        self, file_path: Path, album_id: str | None = None

    ) -> bool:
        """
        Upload a file in chunks with retry mechanism
        """
        file = BunkrrFile.from_path(file_path, album_id=album_id)
        
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
                        msg = f"{file.file_path} upload failed [{attempt+1}/{self._upload_retries}]"
                        logger.error(msg)
                        await asyncio.sleep(self._retry_delay)
                        continue

                    raise FileUploadError(file) from e
        print(f"Successfully uploaded {file.name}")
        return True
        


    # TODO: This should probably move out of API
    async def upload_file(self, file_path: Path, album_id: str | None = None) -> UploadResponse:
        file = BunkrrFile.from_path(file_path, album_id=album_id)
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
