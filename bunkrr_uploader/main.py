import asyncio
import logging

from bunkrr_uploader.config_settings import parse_args
from bunkrr_uploader.logger import setup_logger
from bunkrr_uploader.uploader import BunkrrUploader

logger = logging.getLogger("bunkrr_uploader")


async def async_main() -> None:
    setup_logger()
    args = parse_args()
    logger.debug(f"Using params: \n {args.model_dump_json(indent=4)}")
    bunkrr_client = BunkrrUploader(**args.model_dump())
    try:
        responses = await bunkrr_client.upload(args.path, album_name=args.album_name)
        for r in responses:
            logger.info(r.model_dump_json(indent=4))

    finally:
        await bunkrr_client.close()


def main():
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
