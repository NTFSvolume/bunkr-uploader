import asyncio
import hashlib
import logging
import mimetypes
import os
import uuid
from pathlib import Path
from pprint import pformat
from typing import Any, BinaryIO, Optional

from aiohttp import ClientSession, FormData
from tqdm import tqdm
from tqdm.asyncio import tqdm_asyncio
from yarl import URL

from bunkrr_uploader.api.types.responses import (
    AlbumsResponse,
    CheckResponse,
    CreateAlbumResponse,
    NodeResponse,
    UploadResponse,
    VerifyTokenResponse,
)

from bunkrr_uploader.api.types.custom_types import  BunkrrFile
from bunkrr_uploader.util import ProgressFileReader, TqdmUpTo

logger = logging.getLogger(__name__)


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

        # These all need to be initialized later on before the API is used
        self.max_file_size = 1800
        self.chunk_size = 20
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
        self.retries = retries
        self.max_chunk_retries = options.get("chunk_retries") or 1

    async def _get_json(self, path: str) -> dict:
        async with self._session.get(path) as resp:
            response = await resp.json()
            return response

    async def _post(self, path: str, *, data: FormData | dict | None = None) -> dict:
        data = data or {}
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

    async def upload_chunks(
        self,
        file_data: BinaryIO | ProgressFileReader,
        file: BunkrrFile,
        server: str,
    ) -> None:
        self.chunk_size = self.chunk_size or 20
        total_chunks = (file.size + self.chunk_size - 1) // self.chunk_size
        chunk_index = 0
        dzchunkbyteoffset = 0

        # Iterates all chunks
        while chunk_index < total_chunks:
            with tqdm.external_write_mode():
                logger.debug(f"Processing chunk {chunk_index + 1}/{total_chunks} for {file.name}")

            chunk_data = file_data.read(self.chunk_size)
            chunk_upload_success = False
            chunk_upload_attempt = 0
            if not chunk_data:
                print("No more chunks to upload")
                break  # Exit the loop if we've reached the end of the file

            # likely using https://gitlab.com/meno/dropzone/-/wikis/faq#chunked-uploads
            # https://github.com/Dodotree/DropzonePHPchunks/issues/3
            data = FormData()
            data.add_field("dzuuid", file.uuid)
            data.add_field("dzchunkindex", str(chunk_index))
            data.add_field("dztotalfilesize", str(file.size))
            data.add_field("dzchunksize", str(self.chunk_size))
            data.add_field("dztotalchunkcount", str(total_chunks))
            data.add_field("dzchunkbyteoffset", str(dzchunkbyteoffset))
            data.add_field(
                "files[]",
                chunk_data,
                filename=file.name,
                content_type="application/octet-stream",
            )

            # Retries chunks if they ever fail
            while chunk_upload_attempt < self.max_chunk_retries and chunk_upload_success is False:
                try:
                    response = await self._post("/api/upload", data=data)
                    if response.get("success"):
                        chunk_index += 1
                        dzchunkbyteoffset += self.chunk_size
                        chunk_upload_success = True
                    else:
                        msg = f"{file.uuid} failed uploading chunk #{chunk_index}/{total_chunks} to {server} [{chunk_upload_attempt}/{self.max_chunk_retries}]"
                        with tqdm.external_write_mode():
                            logger.error(msg)
                        raise Exception(msg)
                except Exception:
                    chunk_upload_attempt += 1

            if chunk_upload_success is False:
                msg = f"Failed uploading chunks for {file.uuid} too many times to {server}, cannot continue"
                with tqdm.external_write_mode():
                    logger.error(msg)
                raise Exception(msg)

    # TODO: This should probably move out of API
    async def upload(self, file_path: Path, album_id: str | None = None) -> UploadResponse:
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
            while retries < self.retries:
                try:
                    with open(file_path, "rb") as file_data:
                        with TqdmUpTo(
                            unit="B",
                            unit_scale=True,
                            unit_divisor=1024,
                            miniters=1,
                            desc=f"{file_path.name} [{retries + 1}/{self.retries}]",
                        ) as t:
                            with ProgressFileReader(filename=file_path, read_callback=t.update_to) as file_data:
                                if file.size <= self.chunk_size:
                                    chunk_data = file_data.read(self.chunk_size)
                                    data = FormData()
                                    data.add_field(
                                        "files[]", chunk_data, filename=file_path.name, content_type=file.mimetype
                                    )

                                    async with session.post("/api/upload", data=data, headers=headers) as resp:
                                        response = await resp.json()
                                        if not response.get("success"):
                                            raise Exception(f"{file_path.name} failed uploading without chunks")

                                        return response
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
                                                    msg = f"{file.uuid} failed finishing chunks to {server} [{finish_chunks_attempt + 1}/{self.max_chunk_retries}]\n{pformat(response)}"
                                                    with tqdm.external_write_mode():
                                                        logger.error(msg)
                                                    raise Exception(msg)
                                                # chunk_upload_success = True
                                                response.update(metadata)
                                                return response
                                        except Exception:
                                            finish_chunks_attempt += 1
                                            if finish_chunks_attempt >= self.max_chunk_retries:
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
