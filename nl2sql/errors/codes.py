from enum import Enum


class ErrorCode(str, Enum):
    # --- Safety ---
    SAFETY_NON_SELECT = "SAFETY_NON_SELECT"
    SAFETY_MULTI_STATEMENT = "SAFETY_MULTI_STATEMENT"

    # --- Verifier ---
    PLAN_NO_SUCH_TABLE = "PLAN_NO_SUCH_TABLE"
    PLAN_NO_SUCH_COLUMN = "PLAN_NO_SUCH_COLUMN"
    PLAN_SYNTAX_ERROR = "PLAN_SYNTAX_ERROR"

    # --- Executor / DB ---
    DB_LOCKED = "DB_LOCKED"
    DB_TIMEOUT = "DB_TIMEOUT"

    # --- LLM ---
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_BAD_OUTPUT = "LLM_BAD_OUTPUT"

    # --- Internal ---
    PIPELINE_CRASH = "PIPELINE_CRASH"
