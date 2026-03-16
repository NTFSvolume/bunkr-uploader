from typing import Annotated

from cyclopts import Parameter
from pydantic import BaseModel, ByteSize, Field


class Config(BaseModel, defer_build=True):
    token: Annotated[str, Parameter(alias="-t", env_var="BUNKR_TOKEN")]
    "API token for your account so that you can upload to a specific account/folder. You can also set the BUNKR_TOKEN environment variable for this"

    album_name: Annotated[str | None, Parameter(alias="-n")] = None
    "Name to use for album. If an album with this name already exists, add the files to that album"

    concurrent_uploads: Annotated[int, Parameter(alias="-c")] = Field(2, gt=0, le=50)
    "Maximum parallel uploads to do at once"

    chunk_size: ByteSize | None = Field(default=None, gt=0)
    "Size of chunks to use for uploads. 0 or `None` will use the server's maximum chunk size"

    public: bool = True
    "Make all uploaded files public"

    retries: Annotated[int, Parameter(alias="-R")] = Field(default=1, gt=0)
    "How many times to retry a failed file upload"

    chunk_retries: int = Field(default=2, gt=0)
    "How many times to retry a failed chunk upload"

    delay: float = Field(default=1.0, ge=0)
    "How many seconds to wait in between failed upload attempts"
