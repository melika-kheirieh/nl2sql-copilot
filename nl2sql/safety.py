from __future__ import annotations
import re
import time
from nl2sql.types import StageResult, StageTrace

# --- Regex utils ---
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--.*?$", re.MULTILINE)
# string literals (single & double quotes), allow escaped quotes
_STRING_SINGLE = re.compile(r"'([^'\\]|\\.)*'", re.DOTALL)
_STRING_DOUBLE = re.compile(r'"([^"\\]|\\.)*"', re.DOTALL)

# case-insensitive, word-boundary forbidden keywords
_FORBIDDEN = re.compile(
    r"\b(delete|update|insert|drop|create|alter|attach|pragma|reindex|vacuum|replace|grant|revoke|execute)\b",
    re.IGNORECASE,
)

# allow: SELECT ...   or   WITH <cte...> SELECT ...
_ALLOW_SELECT = re.compile(r"^(?:WITH\b.*?\)\s*)?SELECT\b", re.IGNORECASE | re.DOTALL)


def _strip_comments(s: str) -> str:
    s = _COMMENT_BLOCK.sub(" ", s)
    s = _COMMENT_LINE.sub(" ", s)
    return s


def _mask_strings(s: str) -> str:
    s = _STRING_SINGLE.sub("'X'", s)
    s = _STRING_DOUBLE.sub('"X"', s)
    return s


def _split_statements(s: str) -> list[str]:
    parts = [p.strip() for p in s.split(";")]
    return [p for p in parts if p]


class Safety:
    name = "safety"

    def check(self, sql: str) -> StageResult:
        t0 = time.perf_counter()
        print("🧩 SQL candidate:", sql)
        s = _strip_comments(sql)
        s = _mask_strings(s).strip()

        stmts = _split_statements(s)
        if len(stmts) != 1:
            return StageResult(
                ok=False,
                error=["Multiple statements detected"],
                trace=StageTrace(
                    stage=self.name, duration_ms=(time.perf_counter() - t0) * 1000
                ),
            )

        body = stmts[0]

        if _FORBIDDEN.search(body):
            return StageResult(
                ok=False,
                error=["Forbidden keyword detected"],
                trace=StageTrace(
                    stage=self.name, duration_ms=(time.perf_counter() - t0) * 1000
                ),
            )

        if not _ALLOW_SELECT.match(body):
            return StageResult(
                ok=False,
                error=["Non-SELECT statement"],
                trace=StageTrace(
                    stage=self.name, duration_ms=(time.perf_counter() - t0) * 1000
                ),
            )

        return StageResult(
            ok=True,
            data={
                "sql": sql.strip(),
                "rationale": "Statement validated as SELECT-only (strings/comments ignored).",
            },
            trace=StageTrace(
                stage=self.name, duration_ms=(time.perf_counter() - t0) * 1000
            ),
        )

    run = check
