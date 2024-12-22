# ruff: noqa: N815
from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from hashlib import md5
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ChunkInfo:
    data: bytes
    index: int
    total: int

    @property
    def byte_range(self):
        start=self.index * len(self.data)
        end=(self.index + 1) * len(self.data)
        return (start, end)

@dataclass
class FileInfo:
    path: Path
    album_id: str | None = None
    upload_success: bool = field(init=False, default = False)

    def __post_init__(self):
        self.original_name = self.path.name
        self.upload_name = self.original_name
        self.file_path_MD5: str = md5(str(self.path).encode("utf-8")).hexdigest()
        self.file_name_MD5: str = md5(str(self.path.name).encode("utf-8")).hexdigest()
        self.size = self.path.stat().st_size
        self.mimetype = mimetypes.guess_type(self.path)[0] or "application/octet-stream"
        self.uuid = str(uuid4())


