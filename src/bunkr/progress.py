import contextlib
import dataclasses
from collections.abc import Callable, Generator
from contextvars import ContextVar
from typing import Self

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from bunkr.logger import CONSOLE

_progress: ContextVar[Progress] = ContextVar("_progress")


@contextlib.contextmanager
def new_progress() -> Generator[None]:
    token = _progress.set(
        Progress(
            SpinnerColumn(),
            "[progress.description]{task.description}",
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>6.2f}%",
            "━",
            DownloadColumn(),
            "━",
            TransferSpeedColumn(),
            "━",
            TimeRemainingColumn(),
            console=CONSOLE,
            expand=True,
            transient=True,
        )
    )
    try:
        yield
    finally:
        _progress.reset(token)


@dataclasses.dataclass(slots=True)
class ProgressHook:
    advance: Callable[[int], None]
    done: Callable[[], None]

    _done: bool = dataclasses.field(init=False, default=False)

    def __enter__(self) -> Self:
        if self._done:
            raise RuntimeError
        return self

    def __exit__(self, *_) -> None:
        if self._done:
            raise RuntimeError
        self.done()
        self._done = True


def new_upload(file_name: str, file_size: int) -> ProgressHook:
    progress = _progress.get()
    task_id = progress.add_task(file_name, total=file_size)

    def advance(amount: int = 1) -> None:
        progress.advance(task_id, amount)

    def on_exit() -> None:
        progress.remove_task(task_id)

    return ProgressHook(advance, on_exit)
