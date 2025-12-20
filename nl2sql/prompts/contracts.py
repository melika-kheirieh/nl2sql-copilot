from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# NOTE:
# These are *prompt contracts* (input/output shapes) for LLM-facing stages.
# They are intentionally lightweight to keep Block C minimal and low-risk.


@dataclass(frozen=True)
class PlannerPromptInput:
    user_query: str
    schema_preview: str  # already budgeted at pipeline boundary
    constraints: List[str]


@dataclass(frozen=True)
class PlannerPromptOutput:
    plan: str
    used_tables: List[str]


@dataclass(frozen=True)
class GeneratorPromptInput:
    user_query: str
    schema_preview: str  # already budgeted at pipeline boundary
    plan: str
    constraints: List[str]
    clarify_answers: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class GeneratorPromptOutput:
    sql: str
    rationale: str
    used_tables: List[str]
