from __future__ import annotations

import re
import time
from typing import List, Pattern, Any, cast

import sqlglot
from sqlglot import exp

from nl2sql.types import StageResult, StageTrace
from nl2sql.metrics import safety_blocks_total, safety_checks_total


# ------------------------- Zero-width & basic regexes -------------------------

_ZERO_WIDTH = [
    "\u200b",
    "\u200c",
    "\u200d",
    "\ufeff",
    "\u2060",
    "\u180e",
    "\u200e",
    "\u200f",
]
_ZERO_WIDTH_RE = re.compile("|".join(map(re.escape, _ZERO_WIDTH)))

# String / comment regexes
_STR_SINGLE_RE = re.compile(r"'([^'\\]|\\.)*'", re.DOTALL)
_STR_DOUBLE_RE = re.compile(r'"([^"\\]|\\.)*"', re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Markdown code fences: ```sql\n ... \n```
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\n(?P<body>.*)\n```\s*$", re.DOTALL)

# Strict forbidden keywords (word boundaries)
_FORBIDDEN: Pattern[str] = re.compile(
    r"\b("
    r"delete|update|insert|drop|create|alter|truncate|merge|"
    r"grant|revoke|execute|call|copy|attach|pragma|reindex|vacuum|replace"
    r")\b",
    re.IGNORECASE,
)


def _loose_keyword(pattern: str) -> Pattern[str]:
    r"""
    Build a regex that allows arbitrary whitespace between characters of a keyword.
    Example: "insert" -> i\s*n\s*s\s*e\s*r\s*t
    """
    chars = r"\s*".join(list(pattern))
    return re.compile(rf"\b{chars}\b", re.IGNORECASE)


_FORBIDDEN_LOOSE: List[Pattern[str]] = [
    _loose_keyword(w)
    for w in [
        "delete",
        "update",
        "insert",
        "drop",
        "create",
        "alter",
        "truncate",
        "merge",
        "grant",
        "revoke",
        "execute",
        "call",
        "copy",
        "attach",
        "pragma",
        "reindex",
        "vacuum",
        "replace",
    ]
]

_MAX_SQL_LEN = 200_000  # defensive bound against catastrophic inputs


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _strip_fences(sql: str) -> str:
    m = _FENCE_RE.match(sql)
    return m.group("body") if m else sql


def _collapse_trailing_semicolons(body: str) -> str:
    """
    Keep at most one trailing semicolon. This makes 'SELECT 1;;' equivalent to 'SELECT 1;'.
    """
    body = body.rstrip()
    had_any = False
    while body.endswith(";"):
        had_any = True
        body = body[:-1].rstrip()
    return (body + ";") if had_any else body


def _sanitize(sql: str) -> str:
    """
    Remove zero-width chars, strip markdown fences, trim, and normalize trailing semicolons.
    """
    if not sql:
        return ""
    sql = _ZERO_WIDTH_RE.sub("", sql)
    sql = _strip_fences(sql)
    sql = sql.strip()
    sql = _collapse_trailing_semicolons(sql)
    return sql


def _remove_comments(body: str) -> str:
    body = _BLOCK_COMMENT_RE.sub("", body)
    body = _LINE_COMMENT_RE.sub("", body)
    return body


def _has_comments(body: str) -> bool:
    return bool(_LINE_COMMENT_RE.search(body) or _BLOCK_COMMENT_RE.search(body))


def _contains_forbidden_ast(root: exp.Expression) -> tuple[bool, str]:
    """Return (blocked, reason) based on AST nodes/commands."""
    forbidden_node_names = {
        "insert",
        "update",
        "delete",
        "drop",
        "create",
        "alter",
        "truncate",
        "merge",
        "grant",
        "revoke",
        "execute",
        "call",
        "copy",
        "replace",
    }
    forbidden_command_markers = ("pragma", "attach", "vacuum", "reindex", "analyze")

    try:
        walk = getattr(root, "walk", None)
        if walk is None:
            return False, ""
        for node in root.walk():
            name = type(node).__name__.lower()
            if name in forbidden_node_names:
                return True, name
            if name == "command":
                sql = ""
                try:
                    sql = node.sql(dialect="sqlite").lower()
                except Exception:
                    sql = str(node).lower()
                for kw in forbidden_command_markers:
                    if kw in sql:
                        return True, f"command:{kw}"
    except Exception:
        # If AST walk fails, be conservative: do not block here (parse/root checks already ran).
        return False, ""

    return False, ""


def _strip_strings(body: str) -> str:
    """
    Remove string literals (so forbidden keyword checks won't fire on quoted text).
    """
    body = _STR_SINGLE_RE.sub("''", body)
    body = _STR_DOUBLE_RE.sub('""', body)
    return body


def _count_statements_semicolon(body: str) -> int:
    """
    Count statements by semicolons after removing comments and masking strings.
    """
    masked_strings = _STR_SINGLE_RE.sub("'S'", body)
    masked_strings = _STR_DOUBLE_RE.sub('"S"', masked_strings)
    no_comments = _remove_comments(masked_strings)
    parts = [p.strip() for p in no_comments.split(";")]
    non_empty = [p for p in parts if p]
    return len(non_empty) if non_empty else 0


def _count_statements_sqlglot(body: str) -> int:
    """
    Count statements via sqlglot parser after removing comments.
    """
    try:
        trees = sqlglot.parse(_remove_comments(body))
        return len([t for t in trees if t is not None])
    except Exception:
        # If parse fails, conservatively return 1 to avoid double blocking.
        return 1


class Safety:
    """
    Read-only safety: allow only single-statement SELECT/EXPLAIN (configurable),
    block DML/DDL and multi-statements, detect obfuscations.
    """

    name = "safety"

    def __init__(
        self, allow_explain: bool = True, forbid_comments: bool = False
    ) -> None:
        self.allow_explain = allow_explain
        self.forbid_comments = forbid_comments

    def check(self, sql: str) -> StageResult:
        t0 = time.perf_counter()

        # 0) nil / size guard
        if not sql or not sql.strip():
            safety_blocks_total.labels(reason="empty_sql").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=["empty_sql"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )
        if len(sql) > _MAX_SQL_LEN:
            safety_blocks_total.labels(reason="sql_too_long").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=["sql_too_long"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        # 1) sanitize
        body = _sanitize(sql)

        # 1.5) comment policy (block if any comment tokens are present)
        if self.forbid_comments and _has_comments(body):
            safety_blocks_total.labels(reason="comments_not_allowed").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=["comments_not_allowed"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        # 2) single-statement check (semicolon + parser)
        semicolon_count = _count_statements_semicolon(body)
        glot_count = _count_statements_sqlglot(body)
        if semicolon_count != 1 or glot_count != 1:
            safety_blocks_total.labels(reason="multiple_statements").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=["Multiple statements detected"],
                trace=StageTrace(
                    stage=self.name,
                    duration_ms=_ms(t0),
                    notes={
                        "semicolon_count": semicolon_count,
                        "parser_count": glot_count,
                    },
                ),
            )

        # 3) forbidden keywords (ignore inside string literals)
        scan_body = _strip_strings(body)
        m = _FORBIDDEN.search(scan_body)
        if m:
            tok = m.group(0).strip().lower()
            safety_blocks_total.labels(reason="forbidden_keyword").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=[f"Forbidden: {tok}"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )
        for rx in _FORBIDDEN_LOOSE:
            m2 = rx.search(scan_body)
            if m2:
                tok = m2.group(0).strip().lower()
                safety_blocks_total.labels(reason="forbidden_keyword").inc()
                safety_checks_total.labels(ok="false").inc()
                return StageResult(
                    ok=False,
                    error=[f"Forbidden: {tok}"],
                    trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
                )

        # 4) read-only root kind (SELECT/EXPLAIN[/WITH])
        try:
            trees: list[Any] = sqlglot.parse(body)
            root = cast(exp.Expression, trees[0])
        except Exception as e:
            safety_blocks_total.labels(reason="parse_error").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=["parse_error"],
                trace=StageTrace(
                    stage=self.name, duration_ms=_ms(t0), notes={"parse_error": str(e)}
                ),
            )

        root_type = type(root).__name__.lower()

        # Manual EXPLAIN handling for dialects that parse EXPLAIN to Command
        _EXPLAIN_HEAD_RE = re.compile(r"^\s*explain\s+", re.IGNORECASE)
        if self.allow_explain and _EXPLAIN_HEAD_RE.match(body):
            remainder = _EXPLAIN_HEAD_RE.sub("", body, count=1).lstrip()
            try:
                t2 = cast(exp.Expression, sqlglot.parse_one(remainder))
                t2_type = type(t2).__name__.lower() if t2 else ""
                if t2_type in {"select", "with"}:
                    safety_checks_total.labels(ok="true").inc()
                    return StageResult(
                        ok=True,
                        data={
                            "sql": body,
                            "original_len": len(sql),
                            "sanitized_len": len(body),
                            "allow_explain": True,
                        },
                        trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
                    )
            except Exception:
                # fall through to normal handling
                pass

        is_select_like = root_type in {"select", "with"}
        is_explain = root_type == "explain"

        if is_explain and not self.allow_explain:
            safety_blocks_total.labels(reason="explain_not_allowed").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=["EXPLAIN not allowed"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        if not (is_select_like or (is_explain and self.allow_explain)):
            safety_blocks_total.labels(reason="non_select").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=[f"Non-SELECT statement: {root_type}"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        # 4.5) AST-based forbidden nodes / commands (defense-in-depth)
        blocked, reason = _contains_forbidden_ast(root)
        if blocked:
            safety_blocks_total.labels(reason="forbidden_ast").inc()
            safety_checks_total.labels(ok="false").inc()
            return StageResult(
                ok=False,
                error=[f"Forbidden AST: {reason}"],
                trace=StageTrace(
                    stage=self.name, duration_ms=_ms(t0), notes={"reason": reason}
                ),
            )
        # 5) success
        safety_checks_total.labels(ok="true").inc()
        return StageResult(
            ok=True,
            data={
                "sql": body,
                "original_len": len(sql),
                "sanitized_len": len(body),
                "allow_explain": self.allow_explain,
            },
            trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
        )

    # Keep Pipeline API compatibility (pipeline calls .run(sql=...))
    def run(self, *, sql: str) -> StageResult:
        return self.check(sql)
