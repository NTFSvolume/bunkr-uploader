import logging
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from rich.traceback import install

from bunkr import __package_name__, __version__
from bunkr.client import BunkrUploader
from bunkr.config import Config
from bunkr.logger import setup_logger

logger = logging.getLogger(__name__)


install(width=200)


async def _upload(path: Path, recurse: bool, config: Config) -> None:
    async with setup_logger() as json_logger:
        logger.debug(f'Uploading "{path}"')
        logger.debug(f"Using params: \n {config.model_dump_json(indent=4)}")
        async with BunkrUploader(config, upload_callback=json_logger) as client:
            results = await client.upload(path, recurse=recurse)
            for result in results:
                info = f"success: {result.result.success}, url: {result.result.files[0].url}"
                logger.info(f"{result.file.original_name}: {info}")


app = App(
    name=__package_name__,
    help="Upload files to bunkr.cr",
    version=__version__,
    default_parameter=Parameter(negative_iterable=[]),
)


@app.command()
async def upload(
    path: Annotated[Path, Parameter(help="File or directory to look for files in to upload")],
    /,
    *,
    recurse: Annotated[
        bool, Parameter(help="Read files in PATH recursely", negative_bool=[])
    ] = False,
    config: Annotated[Config, Parameter(name="*")],
) -> None:
    """Upload a file or files to bunkr"""
    await _upload(path, recurse, config)


if __name__ == "__main__":
    app()
