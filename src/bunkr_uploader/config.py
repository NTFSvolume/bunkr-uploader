from pydantic import BaseModel, ByteSize, Field


class Config(BaseModel, defer_build=True):
    token: str = Field(alias="t")
    "API token for your account so that you can upload to a specific account/folder. You can also set the BUNKR_TOKEN environment variable for this"

    album_name: str | None = Field(None, alias="n")
    "Name to use for album. If an album with this name already exists, add the files to that album"

    concurrent_uploads: int = Field(2, alias="c", gt=0, le=50)
    "Maximum parallel uploads to do at once"

    chunk_size: ByteSize | None = None
    "None will use the server's maximum chunk size instead of the default one"

    public: bool = True
    "Make all files uploaded public"

    upload_retries: int = 1
    "How many times to retry a failed file upload"

    chunk_retries: int = 2
    "How many times to retry a failed chunk upload"

    upload_delay: int = 1
    "How many seconds to wait in between failed upload attempts"
