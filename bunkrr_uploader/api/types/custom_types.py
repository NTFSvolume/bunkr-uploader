# ruff: noqa: N815
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import md5
from pathlib import Path
from uuid import uuid4
import mimetypes


@dataclass
class ChunkSize:
    max: str
    default: str
    timeout: int


@dataclass
class FileIdentifierLength:
    min: int
    max: int
    default: int
    force: bool


@dataclass
class StripTags:
    default: bool
    video: bool
    force: bool
    blacklist_extensions: list[str]

@dataclass
class Permissions:
    user: bool
    vip: bool
    vvip: bool
    moderator: bool
    admin: bool
    superadmin: bool

@dataclass(frozen=True)
class Chunk:
    data: bytes
    index: int
    total: int

    @property
    def byte_range(self):
        start=self.index * len(self.data)
        end=(self.index + 1) * len(self.data)
        return (start, end)


@dataclass
class BunkrrFile:
    name: str
    url: str
    file_path: str

    # Properties not preset on official API
    original: str = field(init=False)
    albumid: str = field(init=False)
    file_path_MD5: str = field(init=False)
    file_name_MD5: str = field(init=False)
    upload_success: str = field(init=False)

    size: int = field(init=False)
    mimetype: str = field(init=False,default="application/octet-stream")
    uuid: str = field(init=False)

    @classmethod
    def from_path(cls, file: Path, *, album_id: str | None = None) -> BunkrrFile:
        obj ={
            "fileName": file.name,
            "albumid": album_id,
            "filePath": str(file),
            "filePathMD5": md5(str(file).encode("utf-8")).hexdigest(),
            "fileNameMD5": md5(str(file.name).encode("utf-8")).hexdigest(),
            "uploadSuccess": None,
        }
        

        file_obj = BunkrrFile(**obj)
        file_obj.size = file.stat().st_size
        file_obj.mimetype = mimetypes.guess_type(file)[0] or file_obj.mimetype
        file_obj.uuid = str(uuid4())
        return file_obj
    
    def dump(self) -> dict [str, str]:
        return {
            "fileName": self.name,
            "albumid": self.albumid,
            "filePath": self.file_path,
            "filePathMD5": self.file_path_MD5,
            "fileNameMD5": self.file_name_MD5,
            "uploadSuccess": self.upload_success,
        }

