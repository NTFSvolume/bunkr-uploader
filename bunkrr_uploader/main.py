import asyncio
import logging

from .args import parse_args
from .client import BunkrrUploader
from .logger import setup_logger

logger = logging.getLogger(__name__)


async def async_main() -> None:
    args = parse_args()
    setup_logger()
    logger.debug(f"Using params: \n {args.model_dump_json(indent=4)}")

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
