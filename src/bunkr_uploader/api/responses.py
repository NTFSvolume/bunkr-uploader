# ruff: noqa: N815
import dataclasses
from datetime import datetime, timedelta
from typing import Annotated

import yarl
from pydantic import BaseModel, ByteSize, ConfigDict, HttpUrl, PlainValidator

HttpURL = Annotated[yarl.URL, PlainValidator(lambda x: yarl.URL(str(HttpUrl(x))))]


@dataclasses.dataclass(slots=True)
class ChunkSize:
    max: ByteSize
    default: ByteSize
    timeout: timedelta


@dataclasses.dataclass(slots=True)
class FileIdentifierLength:
    min: int
    max: int
    default: int
    force: bool


@dataclasses.dataclass(slots=True)
class StripTags:
    blacklistExtensions: set[str]
    default: bool
    force: bool
    video: bool


@dataclasses.dataclass(slots=True)
class Permissions:
    admin: bool
    moderator: bool
    superadmin: bool
    user: bool
    vip: bool
    vvip: bool


class _Response(BaseModel):
    model_config = ConfigDict(populate_by_name=True, defer_build=True)
    description: str = ""
    success: bool = True


class FileResponse(_Response):
    name: str
    url: HttpURL | None

    def model_post_init(self, *_) -> None:
        if self.url is None:
            self.success = False


class UploadResponse(_Response):
    files: list[FileResponse] = []


class AlbumItem(_Response):
    descriptionHtml: str
    download: bool
    editedAt: datetime
    enabled: bool
    id: int
    identifier: str
    name: str
    public: bool
    size: ByteSize
    timestamp: datetime
    uploads: int
    zipGeneratedAt: datetime
    zipSize: ByteSize | None


class Albums(_Response):
    albums: list[AlbumItem]
    count: int


class CreateAlbum(_Response):
    id: int


class VerifyToken(_Response):
    defaultRetentionPeriod: timedelta
    group: str
    permissions: Permissions
    retentionPeriods: list[timedelta]
    username: str


class Info(_Response):
    chunkSize: ChunkSize
    defaultTemporaryUploadAge: int
    enableUserAccounts: bool
    fileIdentifierLength: FileIdentifierLength
    maintenance: bool
    maxSize: ByteSize
    private: bool
    stripTags: StripTags
    temporaryUploadAges: list[int]


class Node(_Response):
    url: HttpURL
