import logging

# from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

THEME_DICT = {
    "logging.level.warning": "yellow",
    "logging.level.debug": "blue",
    "logging.level.info": "white",
    "logging.level.error": "red",
}
CONSOLE_THEME = Theme(THEME_DICT)
RICH_CONSOLE = Console(theme=CONSOLE_THEME)
RICH_HANDLER_CONSOLE_CONFIG: dict = {"show_time": False, "rich_tracebacks": False, "tracebacks_show_locals": False}
RICH_HANDLER_FILE_CONFIG: dict = {"show_time": True, "rich_tracebacks": True, "tracebacks_show_locals": True}


def setup_logger() -> None:
    logger = logging.getLogger("bunkrr_uploader")
    logger.setLevel(logging.DEBUG)
    console_handler = RichHandler(**RICH_HANDLER_CONSOLE_CONFIG, level=20, console=RICH_CONSOLE)
    logger.addHandler(console_handler)

    project_folder = Path(__file__).parent
    log_folder = project_folder / "logs"
    log_folder.mkdir(exist_ok=True)
    log_file_path = log_folder / project_folder.with_suffix(".log").name
    log_file_path.unlink(missing_ok=True)
    file_handler = RichHandler(
        **RICH_HANDLER_FILE_CONFIG,
        level=logging.DEBUG,
        console=Console(file=log_file_path.open("a", encoding="utf8"), width=280),
    )
    logger.addHandler(file_handler)
