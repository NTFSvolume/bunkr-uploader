import asyncio
import logging
from typing import NoReturn

from bunkr_uploader.client import BunkrrUploader
from bunkr_uploader.config import parse_args
from bunkr_uploader.logger import setup_logger

logger = logging.getLogger(__name__)


async def async_main() -> None:
    settings = parse_args()
    setup_logger(logger)

    logger.debug(f"Using params: \n {settings.model_dump_json(indent=4)}")
    async with BunkrrUploader(settings) as client:
        results = await client.upload(
            settings.path, settings.recurse, album_name=settings.album_name
        )

        for result in results:
            info = f"success: {result.result.success}, url: {result.result.files[0].url}"
            logger.info(f"{result.file.original_name}: {info}")


def main() -> NoReturn:
    try:
        asyncio.run(async_main())
        exit(0)
    except KeyboardInterrupt:
        logger.warning("Script stopped by user")
        exit(0)
    except Exception:
        logger.exception("Fatal error. Exiting...")
        exit(1)


if __name__ == "__main__":
    main()
