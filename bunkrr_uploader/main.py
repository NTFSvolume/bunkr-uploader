import asyncio
import logging
from pathlib import Path

from bunkrr_uploader.cli import cli
from bunkrr_uploader.client import BunkrrUploader
from bunkrr_uploader.logger import LogConfig, setup_logger

logger = logging.getLogger(__name__)


async def async_main() -> None:
    args = cli()
    setup_logger(
        log_file=LogConfig.USE_MAIN_NAME,
        log_level=logging.DEBUG if args.verbose else logging.INFO,
        logs_folder_overrride=Path(__file__).parents[-3] / "logs",
    )

    logger.debug(dict(vars(args)))

    options = {"save": args.save, "chunk_retries": args.chunk_retries, "use_max_chunk_size": args.max_chunk_size}

    bunkrr_client = BunkrrUploader(args.token, max_connections=args.connections, retries=args.retries, options=options)
    try:
        await bunkrr_client.upload(args.file, album_name=args.folder)
    finally:
        await bunkrr_client.close()


if __name__ == "__main__":
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
