from __future__ import annotations

import re
import time
import unicodedata
from nl2sql.types import StageResult, StageTrace

# --- Regex utils ---
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--.*?$", re.MULTILINE)

# String literals (single & double quotes), allow escaped quotes
_STRING_SINGLE = re.compile(r"'([^'\\]|\\.)*'", re.DOTALL)
_STRING_DOUBLE = re.compile(r'"([^"\\]|\\.)*"', re.DOTALL)

# Case-insensitive, word-boundary forbidden keywords
_FORBIDDEN = re.compile(
    r"\b(delete|update|insert|drop|create|alter|attach|pragma|reindex|vacuum|replace|grant|revoke|execute)\b",
    re.IGNORECASE,
)

# Allow: SELECT ...  or   WITH (one or many CTEs, optional RECURSIVE) ... SELECT ...
_ALLOW_SELECT = re.compile(
    r"^(?:WITH\s+(?:RECURSIVE\s+)?"
    r".*?\)\s*(?:,\s*.*?\)\s*)*"
    r")?SELECT\b",
    re.IGNORECASE | re.DOTALL,
)

# Optional allowance: EXPLAIN SELECT ...
_ALLOW_EXPLAIN_SELECT = re.compile(r"^EXPLAIN\s+SELECT\b", re.IGNORECASE | re.DOTALL)

# --- Cleanup helpers ---
_FENCE_SQL = re.compile(r"```sql", re.IGNORECASE)
_FENCE_ANY = re.compile(r"```")


def _normalize_sql(sql: str) -> str:
    """Normalize to NFKC and strip zero-width characters to prevent obfuscation."""
    s = unicodedata.normalize("NFKC", sql)
    # strip common zero-width spaces/joiners
    return (
        s.replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
    )


def _sanitize_sql(sql: str) -> str:
    """Remove markdown fences, comments, and harmless trailing semicolons."""
    s = _normalize_sql(sql)
    s = _FENCE_SQL.sub("", s)
    s = _FENCE_ANY.sub("", s)
    s = _COMMENT_BLOCK.sub(" ", s)
    s = _COMMENT_LINE.sub(" ", s)
    s = s.strip()
    # remove trailing semicolon safely
    s = s.rstrip(";").strip()
    return s


def _mask_strings(s: str) -> str:
    """Replace string literals so that inner semicolons/keywords don't affect checks."""
    s = _STRING_SINGLE.sub("'X'", s)
    s = _STRING_DOUBLE.sub('"X"', s)
    return s


def _split_statements(s: str) -> list[str]:
    """
    Split on semicolons after string-masking. Ignore empties (e.g., trailing ';').
    """
    parts = [p.strip() for p in s.split(";")]
    return [p for p in parts if p]


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


class Safety:
    name = "safety"

    def __init__(self, allow_explain: bool = False) -> None:
        """
        :param allow_explain: If True, 'EXPLAIN SELECT ...' is allowed in addition to SELECT.
        """
        self.allow_explain = allow_explain

    def check(self, sql: str) -> StageResult:
        t0 = time.perf_counter()

        # 1) Sanitize and mask
        s = _sanitize_sql(sql)
        s = _mask_strings(s).strip()

        # 2) Multiple statements check
        stmts = _split_statements(s)
        if len(stmts) != 1:
            return StageResult(
                ok=False,
                error=["Multiple statements detected"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        body = stmts[0]

        # 3) Forbidden keyword check (report exact offending token)
        m = _FORBIDDEN.search(body)
        if m:
            return StageResult(
                ok=False,
                error=[f"Forbidden keyword detected: '{m.group(0)}'"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        # 4) Allow only SELECT (or optionally EXPLAIN SELECT)
        allowed = bool(_ALLOW_SELECT.match(body))
        if not allowed and self.allow_explain:
            allowed = bool(_ALLOW_EXPLAIN_SELECT.match(body))

        if not allowed:
            return StageResult(
                ok=False,
                error=["Non-SELECT statement"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        # 5) Success
        return StageResult(
            ok=True,
            data={
                "sql": body,
                "rationale": (
                    "Statement validated as SELECT-only (strings/comments/markdown ignored)."
                    + (" EXPLAIN SELECT allowed." if self.allow_explain else "")
                ),
            },
            trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
        )

    # Backward-compat alias
    run = check
