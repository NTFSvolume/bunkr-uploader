from __future__ import annotations

import dataclasses
import datetime
import json
import re
from html import unescape
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self

from yarl import URL

if TYPE_CHECKING:
    from collections.abc import Generator


@dataclasses.dataclass(slots=True)
class File:
    id: str
    name: str
    original: str
    slug: str
    type: str
    extension: str
    size: int
    timestamp: datetime.datetime
    thumbnail: str
    cdnEndpoint: str  # noqa: N815

    src: URL | None = None

    def __post_init__(self) -> None:
        if self.thumbnail.count("https://") != 1:
            return

        if URL(self.thumbnail, encoded="%" in self.thumbnail).parts[1:2] != ("thumbs",):
            return

        src_str = self.thumbnail.replace("/thumbs/", "/")
        src = (
            URL(src_str, encoded="%" in src_str)
            .with_suffix(Path(self.name).suffix)
            .with_query(None)
        )
        if src.suffix.lower() not in _IMAGE_EXTS:
            assert src.host
            src = src.with_host(src.host.replace("i-", ""))
        self.src = src


_translation_map = MappingProxyType(
    {f" {field.name}: ": f'"{field.name}": ' for field in dataclasses.fields(File)}
)
_escape_file_attrs = re.compile(
    "|".join(sorted(_translation_map.keys(), key=len, reverse=True))
).sub


def _fix_unicode(value: Any) -> Any:
    if isinstance(value, str):
        return value.encode("raw_unicode_escape").decode("unicode-escape")
    return value


def _decode_files(text: str) -> Generator[File]:
    content = _escape_file_attrs(lambda m: _translation_map[m.group(0)], text.replace("\\'", "'"))

    file: dict[str, Any]
    for file in json.loads(content):
        file = {k: _fix_unicode(v) for k, v in file.items()}
        timestamp = datetime.datetime.strptime(file.pop("timestamp"), "%H:%M:%S %d/%m/%Y").replace(
            tzinfo=datetime.UTC
        )
        yield File(timestamp=timestamp, **file)


@dataclasses.dataclass(slots=True, order=True)
class Album:
    id: int
    slug: str
    name: str
    files: tuple[File, ...]

    @classmethod
    def parse(cls, slug: str, html: str) -> Self:
        def extr(before: str, after: str) -> str:
            start = html.index(before) + len(before)
            end = html.index(after, start)
            return html[start:end]

        id_ = int(extr('albumID = "', '";'))
        name = unescape(extr('<meta property="og:title" content="', '">'))

        album_js = extr("window.albumFiles = ", "</script>")
        files = _decode_files(album_js[: album_js.rindex("];") + 1])
        return cls(id_, slug, name, tuple(files))

    def __json__(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def __str__(self) -> str:
        import json

        return json.dumps(self.__json__(), indent=2, ensure_ascii=False, default=str)


_IMAGE_EXTS = frozenset(
    {
        ".gif",
        ".gifv",
        ".heic",
        ".jfif",
        ".jif",
        ".jpe",
        ".jpeg",
        ".jpg",
        ".jxl",
        ".png",
        ".svg",
        ".tif",
        ".tiff",
        ".webp",
    }
)
