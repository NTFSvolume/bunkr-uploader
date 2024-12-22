
from bunkrr_uploader.api.types.files import FileInfo, ChunkInfo


class ChunkUploadError(Exception):
    """Custom exception for chunk upload failures"""
    def __init__(self, chunk: ChunkInfo) -> None:
        self.chunk = chunk
        range = chunk.byte_range
        self.message = f"Failed to upload chunk #{self.chunk.index} - Range: b{range[0]}-{range[1]}"
        super().__init__(self.message)

class FileUploadError(Exception):
    """Custom exception for file upload failures"""
    def __init__(self, file: FileInfo) -> None:
        self.file = file
        self.message = f"Failed to upload {self.file.path}"
        super().__init__(self.message)