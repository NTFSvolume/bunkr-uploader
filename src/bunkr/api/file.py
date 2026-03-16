# ruff: noqa: N815
from __future__ import annotations

import dataclasses
import hashlib
import mimetypes
from typing import TYPE_CHECKING
from uuid import uuid4

from bunkr.api.responses import FileResponse

if TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass(slots=True, frozen=True)
class Chunk:
    data: bytes | memoryview[int]
    index: int
    total: int
    offset: int


_MAX_FILENAME_LENGTH: int = 240


@dataclasses.dataclass(slots=True, kw_only=True)
class FileUpload:
    path: Path
    original_name: str
    upload_name: str
    file_path_MD5: str
    file_name_MD5: str
    size: int
    mimetype: str
    uuid: str
    album_id: str | None = None

    upload_success: bool = dataclasses.field(init=False, default=False)

    @staticmethod
    def from_path(path: Path) -> FileUpload:
        original_name = upload_name = path.name

        if len(upload_name) > _MAX_FILENAME_LENGTH:
            max_stem_length = _MAX_FILENAME_LENGTH - len(path.suffix) - 2
            new_stem = upload_name[:max_stem_length] + ".."
            upload_name = f"{new_stem}.{path.suffix}"

        return FileUpload(
            path=path,
            original_name=original_name,
            upload_name=upload_name,
            file_path_MD5=hashlib.md5(str(path).encode("utf-8")).hexdigest(),
            file_name_MD5=hashlib.md5(path.name.encode("utf-8")).hexdigest(),
            size=path.stat().st_size,
            mimetype=mimetypes.guess_type(path)[0] or "application/octet-stream",
            uuid=str(uuid4()),
        )

    def as_response(self) -> FileResponse:
        return FileResponse(name=self.uuid, url=None)
