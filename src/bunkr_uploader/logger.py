from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

if TYPE_CHECKING:
    from bunkr_uploader.client import FileUploadResult


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

json_logger = logging.getLogger("bunkr_output_json")
jsonl_path: Path


def utc_now() -> datetime.datetime:
    return datetime.datetime.now().replace(microsecond=0).astimezone(datetime.UTC)


def setup_logger(logger: logging.Logger) -> None:
    global jsonl_path
    logger.setLevel(logging.DEBUG)
    console_handler = RichHandler(
        show_time=False,
        rich_tracebacks=False,
        tracebacks_show_locals=False,
        level=20,
        console=_CONSOLE,
    )
    logger.addHandler(console_handler)

    log_folder = Path.cwd() / "bunkr_uploader_logs"
    log_folder.mkdir(exist_ok=True)

    now = utc_now().replace(tzinfo=None).isoformat().replace(":", "").replace(" ", "_")
    log_file_path = log_folder / f"{now}.log"
    jsonl_path = log_file_path.with_suffix(".results.json")
    file_handler = RichHandler(
        show_time=True,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        level=logging.DEBUG,
        console=Console(file=log_file_path.open("w", encoding="utf8"), width=500),
    )
    logger.addHandler(file_handler)


def write_to_jsonl(result: FileUploadResult) -> None:
    with jsonl_path.open("a", encoding="utf8") as file_io:
        file_io.write(f"{result.dumps()}\n")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result: FileUploadResult = cast("FileUploadResult", record.msg)
        return result.dumps()
