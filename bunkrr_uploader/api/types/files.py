# ruff: noqa: N815
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from hashlib import md5
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ByteSize, Field

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


class FileInfo(BaseModel):
    name: str
    url: str | None = None
    path: Path | None = None

    original: str = Field(init=False)
    albumid: str = Field(init=False)
    file_path_MD5: str = Field(init=False)
    file_name_MD5: str = Field(init=False)
    upload_success: str = Field(init=False)

    size: int = Field(init=False)
    mimetype: str = Field(init=False,default="application/octet-stream")
    uuid: str = Field(init=False)

    @classmethod
    def from_path(cls, file: Path, *, album_id: str | None = None) -> FileInfo:
        obj ={
            "fileName": file.name,
            "albumid": album_id,
            "filePath": str(file),
            "filePathMD5": md5(str(file).encode("utf-8")).hexdigest(),
            "fileNameMD5": md5(str(file.name).encode("utf-8")).hexdigest(),
            "uploadSuccess": None,
        }

        file_obj = FileInfo(**obj)
        file_obj.size = ByteSize(file.stat().st_size)
        file_obj.mimetype = mimetypes.guess_type(file)[0] or file_obj.mimetype
        file_obj.uuid = str(uuid4())
        return file_obj

