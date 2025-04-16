# ruff: noqa: N815
from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from hashlib import md5
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .responses import UploadItemResponse

if TYPE_CHECKING:
    from pathlib import Path


def _dict(**kwargs: Any) -> dict[str, Any]:
    return kwargs


@dataclass(frozen=True)
class ChunkInfo:
    data: bytes
    index: int
    total: int
    offset: int

    @property
    def byte_range(self):
        start = self.index * len(self.data)
        end = (self.index + 1) * len(self.data)
        return (start, end)


@dataclass
class FileInfo:
    path: Path
    album_id: str | None = None
    max_filename_length: int = 240
    upload_success: bool = field(init=False, default=False)

    def __post_init__(self):
        self.original_name = self.path.name
        self.upload_name = self.original_name
        if len(self.upload_name) > self.max_filename_length:
            max_stem_length = self.max_filename_length - len(self.path.suffix) - 2
            new_stem = self.upload_name[:max_stem_length] + ".."
            self.upload_name = f"{new_stem}{self.path.suffix}"
        self.file_path_MD5: str = md5(str(self.path).encode("utf-8")).hexdigest()
        self.file_name_MD5: str = md5(str(self.path.name).encode("utf-8")).hexdigest()
        self.size = self.path.stat().st_size
        self.mimetype = mimetypes.guess_type(self.path)[0] or "application/octet-stream"
        self.uuid = str(uuid4())

    def dump_json(self) -> dict:
        return _dict(
            uuid=self.uuid,
            original=self.original_name,
            type=self.mimetype,
            albumid=self.album_id or None,
            filelength=None,
            age=None,
        )

    @property
    def as_item(self):
        return UploadItemResponse(name=self.uuid, url=None)
