from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List


@dataclass
class AppError(Exception):
    """Base class for domain-level errors."""

    message: str
    http_status: int = 500
    code: str = "internal_error"
    retryable: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)
    details: Optional[List[str]] = None

    def __str__(self) -> str:
        return self.message


# 4xx
@dataclass
class BadRequestError(AppError):
    http_status: int = 400
    code: str = "bad_request"


@dataclass
class SafetyViolationError(AppError):
    http_status: int = 422
    code: str = "safety_violation"


@dataclass
class SchemaDeriveError(AppError):
    http_status: int = 400
    code: str = "schema_derive_error"


# 5xx-ish
@dataclass
class DependencyError(AppError):
    http_status: int = 503
    code: str = "dependency_error"
    retryable: bool = True


@dataclass
class PipelineConfigError(AppError):
    http_status: int = 500
    code: str = "pipeline_config_error"


@dataclass
class PipelineRunError(AppError):
    http_status: int = 500
    code: str = "pipeline_run_error"


@dataclass
class DbNotFound(BadRequestError):
    code: str = "db_not_found"


@dataclass
class SchemaRequired(BadRequestError):
    code: str = "schema_required"
