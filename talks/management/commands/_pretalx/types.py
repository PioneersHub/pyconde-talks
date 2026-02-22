"""Shared enums and lightweight Pydantic models for the Pretalx importer."""

from enum import Enum
from typing import Protocol

from pydantic import BaseModel
from pytanis.config import PretalxCfg  # noqa: TC002


class VerbosityLevel(Enum):
    """Django management-command verbosity levels (mirrors the built-in ``--verbosity`` flag)."""

    MINIMAL = 0
    NORMAL = 1
    DETAILED = 2
    DEBUG = 3
    TRACE = 4


class LogFn(Protocol):
    """
    Callback signature accepted by :meth:`ImportContext.log`.

    Matches :meth:`LoggingMixin._log`.
    """

    def __call__(
        self,
        message: str,
        verbosity: VerbosityLevel,
        min_level: VerbosityLevel,
        style: str | None = None,
    ) -> None: ...


class PytanisCfg(BaseModel):
    """Pytanis config wrapper - only the Pretalx section is needed."""

    Pretalx: PretalxCfg
