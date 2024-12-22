import asyncio
import logging

from bunkrr_uploader.args import ParsedArgs
from bunkrr_uploader.client import BunkrrUploader
from bunkrr_uploader.logger import LogConfig, setup_logger

logger = logging.getLogger(__name__)


async def async_main() -> None:
    args = ParsedArgs.parse_args()
    setup_logger(log_file=LogConfig.USE_MAIN_NAME)
    logger.debug(args.model_dump_json())

    bunkrr_client = BunkrrUploader(**args.model_dump())
    try:
        await bunkrr_client.upload(args.path, album_name=args.album_name)
    finally:
        await bunkrr_client.close()


def main():
    try:
        asyncio.run(async_main())
        exit(0)
    except KeyboardInterrupt:
        print()
        logger.warning("Script stopped by user")
        exit(0)
    except Exception:
        logger.exception("Fatal error. Exiting...")
        exit(1)


if __name__ == "__main__":
    main()
