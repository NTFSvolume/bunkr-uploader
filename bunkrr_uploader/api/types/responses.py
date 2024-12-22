# ruff: noqa: N815
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ByteSize, HttpUrl
from yarl import URL

HttpURL = Annotated[HttpUrl, AfterValidator(lambda x: URL(str(x)))]


class ChunkSize(BaseModel):
    max: ByteSize
    default: ByteSize
    timeout: timedelta


class FileIdentifierLength(BaseModel):
    min: int
    max: int
    default: int
    force: bool


class StripTags(BaseModel):
    blacklistExtensions: set[str]
    default: bool
    force: bool
    video: bool


class Permissions:
    admin: bool
    moderator: bool
    superadmin: bool
    user: bool
    vip: bool
    vvip: bool


class BunkrrResponse(BaseModel):
    description: str = ""
    success: bool = True


class UploadItemResponse(BunkrrResponse):
    name: str
    url: HttpURL | None


class UploadResponse(BunkrrResponse):
    files: list[UploadItemResponse]


class AlbumItemResponse(BunkrrResponse):
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


class AlbumsResponse(BunkrrResponse):
    albums: list[AlbumItemResponse]
    count: int


class CreateAlbumResponse(BunkrrResponse):
    id: int


class VerifyTokenResponse(BunkrrResponse):
    defaultRetentionPeriod: timedelta
    group: str
    permissions: Permissions
    retentionPeriods: list[timedelta]
    username: str


class CheckResponse(BunkrrResponse):
    chunkSize: ChunkSize
    defaultTemporaryUploadAge: int
    enableUserAccounts: bool
    fileIdentifierLength: FileIdentifierLength
    maintenance: bool
    maxSize: ByteSize
    private: bool
    stripTags: StripTags
    temporaryUploadAges: list[int]


class NodeResponse(BunkrrResponse):
    url: HttpURL
