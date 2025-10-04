import sys
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from pathlib import Path

from pydantic import AliasChoices, ByteSize, Field, ValidationError
from pydantic_settings import BaseSettings, CliPositionalArg, SettingsConfigDict
from rich.console import Console
from rich.text import Text

from bunkr_uploader import __version__

ERROR_PREFIX = "\n[bold red]ERROR: [/bold red]"

console = Console()


def _print_to_console(text: Text | str, *, error: bool = False, **kwargs) -> None:
    msg = (ERROR_PREFIX + text) if error else text  # type: ignore
    console.print(msg, **kwargs)


def _handle_validation_error(
    e: ValidationError, *, title: str | None = None, sources: dict | None = None
) -> None:
    error_count = e.error_count()
    source: Path = sources.get(e.title, None) if sources else None  # type: ignore
    title = title or e.title
    source = f"from {source.resolve()}" if source else ""  # type: ignore
    msg = f"found {error_count} error{'s' if error_count > 1 else ''} parsing {title} {source}"
    _print_to_console(msg, error=True)

    for error in e.errors(include_url=False):
        loc = ".".join(map(str, error["loc"]))
        if title == "CLI arguments":
            loc = error["loc"][-1]
            if isinstance(error["loc"][-1], int):
                loc = ".".join(map(str, error["loc"][-2:]))
            loc = f"--{loc}"
        msg = f"\nValue of '{loc}' is invalid:"
        _print_to_console(msg, markup=False)
        _print_to_console(
            f"  {error['msg']} (input_value='{error['input']}', input_type='{error['type']}')",
            style="bold red",
        )


class ConfigSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="bunkr_",
        env_ignore_empty=True,
        cli_parse_args=True,
        cli_kebab_case=True,
        populate_by_name=True,
    )
    path: CliPositionalArg[Path] = Field(
        description="File or directory to look for files in to upload"
    )
    token: str = Field(
        validation_alias=AliasChoices("t", "token"),
        description="API token for your account so that you can upload to a specific account/folder. You can also set the BUNKR_TOKEN environment variable for this",
    )
    album_name: str | None = Field(None, validation_alias=AliasChoices("n", "album-name"))
    concurrent_uploads: int = Field(
        2,
        validation_alias=AliasChoices("c", "concurrent-uploads"),
        description="Maximum parallel uploads to do at once",
    )
    chunk_size: ByteSize = ByteSize(0)
    use_max_chunk_size: bool = Field(
        True, description="Use the server's maximum chunk size instead of the default one"
    )
    public: bool = Field(True, description="Make all files uploaded public")
    config_file: Path | None = None
    upload_retries: int = Field(1, description="How many times to retry a failed upload")
    chunk_retries: int = Field(
        2, description="How many times to retry a failed chunk or chunk completion"
    )
    upload_delay: int = Field(
        1, description="How many seconds to wait in between failed upload attempts"
    )
    recurse: bool = Field(False, description="Read files in `path` recursely")


def parse_args() -> ConfigSettings:
    """Parses the command line arguments passed into the program."""
    parser = ArgumentParser(
        description="Bulk asynchronous uploader for bunkrr",
        usage="bunkr_uploader [OPTIONS] URL [URL...]",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")

    try:
        return ConfigSettings()  # type: ignore

    except ValidationError as e:
        _handle_validation_error(e, title="CLI arguments")
        sys.exit(1)
