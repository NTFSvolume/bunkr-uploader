from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable


_CONSOLE = Console(
    theme=Theme(
        {
            "logging.level.warning": "yellow",
            "logging.level.debug": "blue",
            "logging.level.info": "white",
            "logging.level.error": "red",
        }
    )
)


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0)


@contextlib.asynccontextmanager
async def setup_logger() -> AsyncGenerator[Callable[[object], None]]:
    logger = logging.getLogger("bunkr")
    logger.setLevel(logging.DEBUG)
    console_handler = RichHandler(
        show_time=False,
        rich_tracebacks=False,
        tracebacks_show_locals=False,
        level=logging.WARNING,
        console=_CONSOLE,
    )
    logger.addHandler(console_handler)

    log_folder = Path.cwd() / "bunkr_uploader_logs"
    log_folder.mkdir(exist_ok=True)

    now = utc_now().replace(tzinfo=None).isoformat().replace(":", "").replace(" ", "_")
    log_file_path = log_folder / f"{now}.log"
    jsonl_path = log_file_path.with_suffix(".results.jsonl")
    with log_file_path.open("w", encoding="utf8") as file_out:
        file_handler = RichHandler(
            show_time=True,
            rich_tracebacks=True,
            tracebacks_show_locals=True,
            level=logging.DEBUG,
            console=Console(file=file_out, width=500),
        )
        logger.addHandler(file_handler)
        async with asyncio.TaskGroup() as tg:

            def write_jsonl(result: object) -> None:
                def dump():
                    with jsonl_path.open("a", encoding="utf8") as file_io:
                        file_io.write(f"{result!s}\n")

                _ = tg.create_task(asyncio.to_thread(dump))

            yield write_jsonl
