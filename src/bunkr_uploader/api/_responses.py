# ruff: noqa: N815
from datetime import datetime, timedelta
from typing import Annotated, TypedDict

import yarl
from pydantic import BaseModel, ByteSize, ConfigDict, HttpUrl, PlainValidator

URL = Annotated[yarl.URL, PlainValidator(lambda x: yarl.URL(str(HttpUrl(x))))]


class ChunkSize(BaseModel):
    max: ByteSize
    default: ByteSize
    timeout: timedelta


class FileIdentifierLength(TypedDict):
    min: int
    max: int
    default: int
    force: bool


class StripTags(TypedDict):
    blacklistExtensions: set[str]
    default: bool
    force: bool
    video: bool


class Permissions(TypedDict):
    admin: bool
    moderator: bool
    superadmin: bool
    user: bool
    vip: bool
    vvip: bool


class BunkrrResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, defer_build=True)
    description: str = ""
    success: bool = True


class UploadItemResponse(BunkrrResponse):
    name: str
    url: URL | None

    def model_post_init(self, *_) -> None:
        if self.url is None:
            self.success = False


class Upload(BunkrrResponse):
    files: list[UploadItemResponse] = []


class AlbumItem(BunkrrResponse):
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


class Albums(BunkrrResponse):
    albums: list[AlbumItem]
    count: int


class CreateAlbum(BunkrrResponse):
    id: int


class VerifyToken(BunkrrResponse):
    defaultRetentionPeriod: timedelta
    group: str
    permissions: Permissions
    retentionPeriods: list[timedelta]
    username: str


class Check(BunkrrResponse):
    chunkSize: ChunkSize
    defaultTemporaryUploadAge: int
    enableUserAccounts: bool
    fileIdentifierLength: FileIdentifierLength
    maintenance: bool
    maxSize: ByteSize
    private: bool
    stripTags: StripTags
    temporaryUploadAges: list[int]


class Node(BunkrrResponse):
    url: URL
