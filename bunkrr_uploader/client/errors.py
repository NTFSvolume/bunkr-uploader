from bunkrr_uploader.api.types.files import FileInfo


class FileUploadError(Exception):
    """Custom exception for file upload failures"""

    def __init__(self, file: FileInfo) -> None:
        self.file = file
        self.message = f"Failed to upload {self.file.path}"
        super().__init__(self.message)
