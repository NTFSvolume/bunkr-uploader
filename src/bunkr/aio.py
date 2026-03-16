"""Async versions of builtins and some path operations"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable
from typing import IO, TYPE_CHECKING, Any, AnyStr, Generic, Self, overload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Iterable
    from pathlib import Path

    from _typeshed import OpenBinaryMode, OpenTextMode


@dataclasses.dataclass(slots=True, eq=False)
class AsyncIO(Generic[AnyStr]):
    """An asynchronous context manager wrapper for a file object."""

    _coro: Awaitable[IO[AnyStr]]
    _io: IO[AnyStr] = dataclasses.field(init=False)

    async def __aenter__(self) -> Self:
        self._io = await self._coro
        return self

    async def __aexit__(self, *_) -> None:
        return await asyncio.to_thread(self._io.close)

    async def __aiter__(self) -> AsyncIterator[AnyStr]:
        while True:
            line = await self.readline()
            if line:
                yield line
            else:
                break

    async def read(self, size: int = -1) -> AnyStr:
        return await asyncio.to_thread(self._io.read, size)

    async def readline(self) -> AnyStr:
        return await asyncio.to_thread(self._io.readline)

    async def readlines(self) -> list[AnyStr]:
        return await asyncio.to_thread(self._io.readlines)

    async def write(self, b: AnyStr, /) -> int:
        return await asyncio.to_thread(self._io.write, b)

    async def writelines(self, lines: Iterable[AnyStr], /) -> None:
        return await asyncio.to_thread(self._io.writelines, lines)


@overload
def open(
    path: Path,
    mode: OpenBinaryMode,
    buffering: int = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
    newline: str | None = ...,
) -> AsyncIO[bytes]: ...


@overload
def open(
    path: Path,
    mode: OpenTextMode = ...,
    buffering: int = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
    newline: str | None = ...,
) -> AsyncIO[str]: ...


def open(
    path: Path,
    mode: str = "r",
    buffering: int = -1,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> AsyncIO[Any]:
    coro = asyncio.to_thread(path.open, mode, buffering, encoding, errors, newline)
    return AsyncIO(coro)
