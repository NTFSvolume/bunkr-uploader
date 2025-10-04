import sys
from pathlib import Path

import rich
from pydantic import AliasChoices, ByteSize, Field, ValidationError
from pydantic_settings import BaseSettings, CliPositionalArg, SettingsConfigDict

ERROR_PREFIX = "\n[bold red]ERROR: [/bold red]"


def _print_to_console(text: str, *, error: bool = False, **kwargs) -> None:
    msg = (ERROR_PREFIX + text) if error else text
    rich.print(msg, **kwargs)


def _handle_validation_error(exception: ValidationError) -> None:
    error_count = exception.error_count()
    msg = f"found {error_count} error{'s' if error_count > 1 else ''} parsing {exception.title}"
    _print_to_console(msg, error=True)

    for error in exception.errors(include_url=False):
        loc = error["loc"][-1]
        if isinstance(error["loc"][-1], int):
            loc = ".".join(map(str, error["loc"][-2:]))
        loc = f"--{str(loc).replace('_', '-')}"

        msg = f"\nValue of '{loc}' is invalid:"
        _print_to_console(msg, markup=False)
        _print_to_console(
            f"  {error['msg']} (input_value='{error['input']}')\n",
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
    try:
        return ConfigSettings()  # type: ignore

    except ValidationError as e:
        _handle_validation_error(e)
        sys.exit(1)
