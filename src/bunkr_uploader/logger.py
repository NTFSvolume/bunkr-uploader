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


def setup_logger(name: str) -> None:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    console_handler = RichHandler(
        show_time=False,
        rich_tracebacks=False,
        tracebacks_show_locals=False,
        level=20,
        console=_CONSOLE,
    )
    logger.addHandler(console_handler)

    log_folder = Path.cwd() / f"{name}_logs"
    log_folder.mkdir(exist_ok=True)

    now = datetime.datetime.now().isoformat().replace(":", "").replace(" ", "_")
    log_file_path = log_folder / f"{now}.log"
    log_file_path.unlink(missing_ok=True)
    file_handler = RichHandler(
        show_time=True,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        level=logging.DEBUG,
        console=Console(file=log_file_path.open("a", encoding="utf8"), width=500),
    )
    logger.addHandler(file_handler)

    json_file_handler = logging.FileHandler(log_file_path.with_suffix("results.jsonl"))
    json_file_handler.setFormatter(JsonFormatter())
    json_logger.addHandler(json_file_handler)
    json_logger.setLevel(10)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result: FileUploadResult = cast("FileUploadResult", record.msg)
        return result.dumps()
