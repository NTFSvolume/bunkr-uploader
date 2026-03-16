from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bunkr.api.file import Chunk, FileUpload


class BunkrUploaderError(Exception): ...


class ChunkUploadError(BunkrUploaderError):
    def __init__(self, file: FileUpload, chunk: Chunk) -> None:
        self.message = f"Failed uploading chunk #{chunk.index + 1}/{chunk.total} of {file.uuid}({file.original_name})"
        super().__init__(self.message)


class FileUploadError(BunkrUploaderError):
    def __init__(self, file: FileUpload) -> None:
        self.message = f"Failed to upload {file.path}"
        super().__init__(self.message)
