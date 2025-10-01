# ruff: noqa: N815
from __future__ import annotations

import dataclasses
import hashlib
import mimetypes
from typing import TYPE_CHECKING
from uuid import uuid4

from ._responses import UploadItemResponse

if TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass(slots=True, frozen=True)
class Chunk:
    data: bytes
    index: int
    total: int
    offset: int


_MAX_FILENAME_LENGTH: int = 240


@dataclasses.dataclass(slots=True, kw_only=True)
class File:
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
    def from_path(path: Path) -> File:
        original_name = upload_name = path.name

        if len(upload_name) > _MAX_FILENAME_LENGTH:
            max_stem_length = _MAX_FILENAME_LENGTH - len(path.suffix) - 2
            new_stem = upload_name[:max_stem_length] + ".."
            upload_name = f"{new_stem}.{path.suffix}"

        return File(
            path=path,
            original_name=original_name,
            upload_name=upload_name,
            file_path_MD5=hashlib.md5(str(path).encode("utf-8")).hexdigest(),
            file_name_MD5=hashlib.md5(path.name.encode("utf-8")).hexdigest(),
            size=path.stat().st_size,
            mimetype=mimetypes.guess_type(path)[0] or "application/octet-stream",
            uuid=str(uuid4()),
        )

    def payload(self) -> dict[str, str | None]:
        return {
            "uuid": self.uuid,
            "original": self.original_name,
            "type": self.mimetype,
            "albumid": self.album_id or None,
            "filelength": None,
            "age": None,
        }

    def as_item(self) -> UploadItemResponse:
        return UploadItemResponse(name=self.uuid, url=None)
