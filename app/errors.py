from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppError(Exception):
    """Base class for domain-level errors."""

    message: str

    def __str__(self) -> str:
        return self.message


# 4xx-ish
@dataclass
class DbNotFound(AppError):
    """Requested DB (or db_id) does not exist."""


@dataclass
class InvalidRequest(AppError):
    """User input is invalid or cannot be processed."""


@dataclass
class SchemaRequired(AppError):
    """Caller must provide schema_preview (e.g. postgres mode)."""


@dataclass
class SchemaDeriveError(AppError):
    """Failed to derive schema preview from DB."""


# 5xx-ish
@dataclass
class PipelineConfigError(AppError):
    """Pipeline/YAML/config is missing or malformed."""


@dataclass
class PipelineRunError(AppError):
    """Unexpected failure while running the pipeline."""
