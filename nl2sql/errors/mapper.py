from nl2sql.errors.codes import ErrorCode

ERROR_MAP = {
    ErrorCode.SAFETY_NON_SELECT: (422, False),
    ErrorCode.SAFETY_MULTI_STATEMENT: (422, False),
    ErrorCode.PLAN_NO_SUCH_TABLE: (422, False),
    ErrorCode.PLAN_NO_SUCH_COLUMN: (422, False),
    ErrorCode.PLAN_SYNTAX_ERROR: (422, False),
    ErrorCode.DB_LOCKED: (503, True),
    ErrorCode.DB_TIMEOUT: (503, True),
    ErrorCode.LLM_TIMEOUT: (503, True),
    ErrorCode.PIPELINE_CRASH: (500, False),
}


def map_error(code: ErrorCode | None) -> tuple[int, bool]:
    if code is None:
        return (500, False)
    return ERROR_MAP.get(code, (500, False))
