import asyncio
import csv
import logging
import re
import tempfile
import time
from pathlib import Path
from pprint import pformat, pprint
from typing import Any, List, Optional

from .api import BunkrrAPI
from .cli import cli
from .logging_manager import USE_MAIN_NAME, setup_logger

logger = logging.getLogger(__name__)

async def async_main() -> None:
    args = cli()
    setup_logger(
        log_file=USE_MAIN_NAME,
        log_level=logging.DEBUG if args.verbose else logging.INFO,
        logs_folder_overrride=Path(__file__).parents[-3] / "logs",
    )

    logger.debug(dict(vars(args)))

    options = {"save": args.save, "chunk_retries": args.chunk_retries, "use_max_chunk_size": args.max_chunk_size}

    bunkrr_client = BunkrrUploader(args.token, max_connections=args.connections, retries=args.retries, options=options)
    try:
        await bunkrr_client.init()
        if args.dry_run:
            logger.warning("Dry run only, uploading skipped")
        else:
            await bunkrr_client.upload_files(args.file, folder=args.folder)
    finally:
        if not bunkrr_client.api._session.closed:
            await bunkrr_client.api._session.close()
        for server_session in bunkrr_client.api.server_sessions.values():
            if not server_session.closed:
                server_session.close()


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

    
