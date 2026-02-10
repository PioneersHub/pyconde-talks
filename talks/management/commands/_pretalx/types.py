"""Shared enums and lightweight Pydantic models for the Pretalx importer."""

from enum import Enum

from pydantic import BaseModel
from pytanis.config import PretalxCfg  # noqa: TC002


class VerbosityLevel(Enum):
    """Enumeration for Django management command verbosity levels."""

    MINIMAL = 0
    NORMAL = 1
    DETAILED = 2
    DEBUG = 3
    TRACE = 4


class PytanisCfg(BaseModel):
    """Pytanis config wrapper - only the Pretalx section is needed."""

    Pretalx: PretalxCfg
