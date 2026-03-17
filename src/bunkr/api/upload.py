# ruff: noqa: N815
from __future__ import annotations

import dataclasses
import hashlib
import mimetypes
from pathlib import Path  # noqa: TC003
from uuid import uuid4

from bunkr.api.responses import FileResponse

_MAX_FILENAME_LENGTH: int = 240


def _truncate_name(path: Path) -> str:
    if len(path.name) <= _MAX_FILENAME_LENGTH:
        return path.name

    max_bytes = _MAX_FILENAME_LENGTH - len(path.suffix) - 2
    new_stem = path.name.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return f"{new_stem}..{path.suffix}"


@dataclasses.dataclass(slots=True, eq=False)
class Chunk:
    data: bytes | memoryview[int] | bytearray
    index: int
    total: int
    offset: int


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
    def create(path: Path, size: int) -> FileUpload:
        return FileUpload(
            path=path,
            original_name=path.name,
            upload_name=_truncate_name(path),
            file_path_MD5=hashlib.md5(path.as_posix().encode("utf-8")).hexdigest(),
            file_name_MD5=hashlib.md5(path.name.encode("utf-8")).hexdigest(),
            size=size,
            mimetype=mimetypes.guess_type(path)[0] or "application/octet-stream",
            uuid=str(uuid4()),
        )

    def as_failed_resp(self) -> FileResponse:
        return FileResponse(name=self.uuid, url=None)
