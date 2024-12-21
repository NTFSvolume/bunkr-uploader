
from dataclasses import dataclass
from .custom_types import BunkrrFile, ChunkSize, Permissions, FileIdentifierLength, StripTags

@dataclass
class BunkrrResponse:
    success: bool

@dataclass
class UploadResponse(BunkrrResponse):
    files: list[BunkrrFile]


@dataclass
class AlbumItemResponse(BunkrrResponse):
    id: int
    name: str
    identifier: str


@dataclass
class AlbumsResponse(BunkrrResponse):
    albums: list[AlbumItemResponse]
    count: int


@dataclass
class CreateAlbumResponse(BunkrrResponse):
    id: int


@dataclass
class VerifyTokenResponse(BunkrrResponse):
    username: str
    permissions: Permissions
    group: str
    retention_periods: list[int]
    default_retention_period: int



@dataclass
class CheckResponse(BunkrrResponse):
    private: bool
    enable_user_accounts: bool
    max_size: str
    chunk_size: ChunkSize
    file_identifier_length: FileIdentifierLength
    strip_tags: StripTags
    temporary_upload_ages: list[int]
    default_temporary_upload_age: int


@dataclass
class NodeResponse(BunkrrResponse):
    url: str