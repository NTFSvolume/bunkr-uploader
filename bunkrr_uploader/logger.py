import logging
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

CONSOLE_THEME = Theme(
    {
        "logging.level.warning": "yellow",
        "logging.level.debug": "blue",
        "logging.level.info": "white",
        "logging.level.error": "red",
    },
)

RICH_CONSOLE = Console(theme=CONSOLE_THEME)
RICH_HANDLER_CONFIG: dict = {"show_time": False, "rich_tracebacks": True, "tracebacks_show_locals": False}


def setup_logger(
    log_level: int = logging.DEBUG,
    use_rich_console: bool = True,
) -> None:
    urllib3_logger = logging.getLogger("urllib3")
    urllib3_logger.setLevel(logging.CRITICAL)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    if use_rich_console:
        console_handler = RichHandler(**RICH_HANDLER_CONFIG, level=log_level, console=RICH_CONSOLE)
        logger.addHandler(console_handler)

    project_folder = Path(__file__).parent
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_folder = project_folder / "logs"

    log_file_path = log_folder / project_folder.with_suffix(".log").name
    log_file_path = log_file_path.parent / f"{log_file_path.stem}_{current_time}.log"
    log_file_path.parent.mkdir(exist_ok=True)
    file_handler = RichHandler(
        **RICH_HANDLER_CONFIG,
        level=logging.DEBUG,
        console=Console(file=log_file_path.open("a", encoding="utf8")),
    )
    logger.addHandler(file_handler)


if __name__ == "__main__":
    raise NotImplementedError
