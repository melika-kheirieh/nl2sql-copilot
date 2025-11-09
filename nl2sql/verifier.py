from __future__ import annotations
import re
import time
from typing import Any, Iterable, List, Optional, Dict, Tuple

import sqlglot
from sqlglot import expressions as exp

from nl2sql.types import StageResult, StageTrace
from nl2sql.metrics import (
    verifier_checks_total,
    verifier_failures_total,
)


def _ms(t0: float) -> int:
    """Return elapsed milliseconds since t0, as int."""
    return int((time.perf_counter() - t0) * 1000)


# ---------------- Small Levenshtein distance for schema matching ----------------
def _lev(a: str, b: str) -> int:
    n = len(b)

    dp = list(range(n + 1))
    for i, ca in enumerate(a, 1):
        prev, dp[0] = dp[0], i
        for j, cb in enumerate(b, 1):
            cur = min(
                dp[j] + 1,  # delete
                dp[j - 1] + 1,  # insert
                prev + (0 if ca == cb else 1),  # replace
            )
            prev, dp[j] = dp[j], cur
    return dp[n]


def _closest(name: str, candidates: List[str]) -> Tuple[str, int]:
    """Find the closest match (by edit distance) for a given name."""
    best, dist = name, 10**9
    for c in candidates:
        d = _lev(name.lower(), c.lower())
        if d < dist:
            best, dist = c, d
    return best, dist


def _maybe_singular(plural: str, tables: List[str]) -> Optional[str]:
    """Simple singularization heuristic: 'singers' -> 'singer'."""
    if plural.endswith("s"):
        cand = plural[:-1]
        if cand in tables:
            return cand
    return None


# ---------------- Verifier with schema-aware repair ----------------
class Verifier:
    name = "verifier"

    # Aggregate call detector used by both AST and regex fallbacks
    _AGG_CALL_RE = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)

    # Fast token sanity: require SELECT and FROM to exist in the cleaned SQL
    _REQ_SELECT = re.compile(r"\bselect\b", re.IGNORECASE)
    _REQ_FROM = re.compile(r"\bfrom\b", re.IGNORECASE)

    # ---------- AST helpers ----------
    def _walk(self, node: exp.Expression) -> Iterable[exp.Expression]:
        """Depth-first traversal of a SQLGlot AST."""
        stack = [node]
        while stack:
            cur = stack.pop()
            if isinstance(cur, exp.Expression):
                yield cur
                args = getattr(cur, "args", {}) or {}
                for v in args.values():
                    if isinstance(v, exp.Expression):
                        stack.append(v)
                    elif isinstance(v, list):
                        for it in v:
                            if isinstance(it, exp.Expression):
                                stack.append(it)

    def _first_select(self, tree: exp.Expression) -> Optional[exp.Select]:
        """Return the first SELECT node from the AST (if any)."""
        for n in self._walk(tree):
            if isinstance(n, exp.Select):
                return n
        return None

    def _has_group_by(self, tree: exp.Expression) -> bool:
        sel = self._first_select(tree)
        return bool(getattr(sel, "group", None)) if sel else False

    def _is_distinct_projection(self, tree: exp.Expression) -> bool:
        sel = self._first_select(tree)
        if not sel:
            return False
        if getattr(sel, "distinct", None):
            return True
        return any(isinstance(n, exp.Distinct) for n in self._walk(sel))

    def _has_windowed_aggregate(self, tree: exp.Expression) -> bool:
        return any(isinstance(n, exp.Window) for n in self._walk(tree))

    def _expr_contains_agg(self, node: exp.Expression) -> bool:
        """Return True if an expression contains an aggregate function."""
        agg_names = {"count", "sum", "avg", "min", "max"}
        agg_type_names = (
            "Count",
            "Sum",
            "Avg",
            "Min",
            "Max",
            "GroupConcat",
            "ArrayAgg",
            "StringAgg",
        )
        agg_types = tuple(
            t
            for t in (getattr(exp, n, None) for n in agg_type_names)
            if isinstance(t, type)
        )

        # AST type-based check (preferred)
        if agg_types and any(isinstance(n, agg_types) for n in self._walk(node)):
            return True

        # Fallback: function-like name check
        Anonymous = getattr(exp, "Anonymous", None)
        func_like = (exp.Func,) + ((Anonymous,) if isinstance(Anonymous, type) else ())

        def _fname(n: exp.Expression) -> str:
            nm = getattr(n, "name", None)
            if isinstance(nm, str) and nm:
                return nm.lower()
            this = getattr(n, "this", None)
            if isinstance(this, str):
                return this.lower()
            this_name = getattr(this, "name", None)
            if isinstance(this_name, str) and this_name:
                return this_name.lower()
            return (str(this) or "").lower()

        for n in self._walk(node):
            if isinstance(n, func_like) and _fname(n) in agg_names:
                return True
        return False

    def _clean_sql_for_fn_scan(self, sql: str) -> str:
        """Normalize SQL before scanning for function names or keywords."""
        s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)  # block comments
        s = re.sub(r"--.*?$", " ", s, flags=re.MULTILINE)  # line comments
        s = re.sub(
            r"('([^']|'')*'|\"([^\"]|\"\")*\"|`[^`]*`)", " ", s
        )  # quoted strings
        s = re.sub(r"\s+", " ", s).strip()
        return s

    # ---------------- Schema-Guard Repair ----------------
    def _schema_dict(self, adapter: Any) -> Optional[Dict[str, List[str]]]:
        """Fetch schema dict {table: [columns]} from adapter if available."""
        if not adapter:
            return None
        get = getattr(adapter, "schema_dict", None)
        if callable(get):
            try:
                d = get()
                if isinstance(d, dict):
                    return {str(k): list(v) for k, v in d.items()}
            except Exception:
                return None
        return None

    def _repair_with_schema(
        self, sql: str, schema: Dict[str, List[str]]
    ) -> Tuple[str, bool, List[str]]:
        """Try to fix table/column names using schema similarity (singularize + closest edit-distance <= 2)."""
        notes: List[str] = []
        try:
            ast = sqlglot.parse_one(sql)
        except Exception as e:
            return sql, False, [f"parse_error:{e!s}"]

        tables = list(schema.keys())
        changed = False

        # Fix table names
        def _fix_table(node: exp.Expression) -> exp.Expression:
            nonlocal changed
            if isinstance(node, exp.Table):
                orig = node.name
                if orig in schema:
                    return node
                s1 = _maybe_singular(orig, tables)
                if s1:
                    changed = True
                    return exp.Table(this=sqlglot.to_identifier(s1))
                best, dist = _closest(orig, tables)
                if dist <= 2:
                    changed = True
                    return exp.Table(this=sqlglot.to_identifier(best))
            return node

        ast = ast.transform(_fix_table)

        # Fix column names
        def _fix_col(node: exp.Expression) -> exp.Expression:
            nonlocal changed
            if isinstance(node, exp.Column):
                name = node.name
                if not name:
                    return node
                tbl = node.table
                if tbl and tbl in schema:
                    candidates = schema[tbl]
                else:
                    candidates = [c for cols in schema.values() for c in cols]
                if name in candidates:
                    return node
                best, dist = _closest(name, candidates) if candidates else (name, 99)
                if dist <= 2:
                    changed = True
                    node.set("this", sqlglot.to_identifier(best))
            return node

        ast = ast.transform(_fix_col)

        if not changed:
            return sql, True, notes

        try:
            repaired = ast.sql(dialect="sqlite")
        except Exception as e:
            return sql, False, notes + [f"rebuild_error:{e!s}"]

        notes.append("schema_guard_repair")
        return repaired, True, notes

    # ---------------- Main verifier logic ----------------
    def verify(
        self, sql: str, *, exec_result: Any = None, adapter: Any = None
    ) -> StageResult:
        """
        Verify syntax, basic semantics, and optionally schema correctness and preview-execution.

        Returns:
          StageResult with:
            - ok: boolean
            - data: may include {"verified": True, "sql": <repaired_sql>}
            - trace: StageTrace(stage="verifier", duration_ms=...)
        """
        t0 = time.perf_counter()
        issues: List[str] = []
        repaired_sql = None

        # 0) Fast token sanity: must contain SELECT and FROM (handles typos like SELCT/FRM).
        sql_scan = self._clean_sql_for_fn_scan(sql)
        if not self._REQ_SELECT.search(sql_scan) or not self._REQ_FROM.search(sql_scan):
            verifier_checks_total.labels(ok="false").inc()
            verifier_failures_total.labels(reason="parse_error").inc()
            return StageResult(
                ok=False,
                error=["parse_error"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        # 1) Syntax validation via sqlglot
        try:
            tree = sqlglot.parse_one(sql, read=None)
            if tree is None:
                return StageResult(
                    ok=False,
                    error=["parse_error"],
                    trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
                )
            tree_type = type(tree).__name__
            if tree_type in ("Command", "Unknown"):
                verifier_checks_total.labels(ok="false").inc()
                verifier_failures_total.labels(reason="parse_error").inc()
                return StageResult(
                    ok=False,
                    error=["parse_error"],
                    trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
                )
        except Exception:
            verifier_checks_total.labels(ok="false").inc()
            verifier_failures_total.labels(reason="parse_error").inc()
            return StageResult(
                ok=False,
                error=["parse_error"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        # 2) Semantic rule: avoid aggregate + non-aggregate mix without GROUP BY (unless DISTINCT/window)
        try:
            sel = self._first_select(tree)
            if sel:
                has_group = self._has_group_by(tree)
                has_window = self._has_windowed_aggregate(tree)
                is_distinct = self._is_distinct_projection(tree)
                select_items = list(getattr(sel, "expressions", []) or [])
                any_agg = any(self._expr_contains_agg(it) for it in select_items)
                any_nonagg_col = any(
                    (
                        any(isinstance(n, exp.Column) for n in self._walk(it))
                        and not self._expr_contains_agg(it)
                    )
                    for it in select_items
                )
                if (
                    any_agg
                    and any_nonagg_col
                    and not (has_group or has_window or is_distinct)
                ):
                    verifier_failures_total.labels(reason="semantic_error").inc()
                    issues.append("aggregation_without_group_by")
        except Exception as e:
            verifier_failures_total.labels(reason="semantic_error").inc()
            issues.append(f"semantic_check_error:{e!s}")
        # 2b) Regex fallback for aggregate + non-aggregate without GROUP BY.
        #     Skip if DISTINCT or any WINDOW (OVER ...) is present in the SELECT list.
        try:
            low = sql_scan.lower()
            if "group by" not in low and "distinct" not in low:
                m = re.search(
                    r"select\s+(?P<sel>.+?)\s+from\b",
                    sql_scan,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if m:
                    sel_clause = m.group("sel")
                    # If window functions are present, allow (COUNT(*) OVER (...), etc.)
                    if re.search(r"\bover\b", sel_clause, flags=re.IGNORECASE):
                        pass  # windowed aggregates are acceptable without GROUP BY
                    else:
                        has_agg = bool(self._AGG_CALL_RE.search(sel_clause))
                        # Heuristic: presence of a comma OR a bare identifier besides pure aggregate-only select
                        has_bare_col = "," in sel_clause or (
                            bool(re.search(r"\b[a-zA-Z_][\w.]*\b", sel_clause))
                            and not re.fullmatch(
                                r"\s*(count|sum|avg|min|max)\s*\([^)]*\)\s*",
                                sel_clause,
                                flags=re.IGNORECASE,
                            )
                        )
                        if (
                            has_agg
                            and has_bare_col
                            and "aggregation_without_group_by" not in issues
                        ):
                            verifier_failures_total.labels(
                                reason="semantic_error"
                            ).inc()
                            issues.append("aggregation_without_group_by")
        except Exception:
            # Non-fatal; AST path already attempted.
            pass

        # 3) Schema-based auto-repair (optional)
        schema = self._schema_dict(adapter)
        if schema:
            fixed, ok_fix, notes = self._repair_with_schema(sql, schema)
            if ok_fix is True and fixed != sql:
                repaired_sql = fixed
            if notes:
                issues.extend(
                    [f"note:{n}" for n in notes if not n.startswith("parse_error")]
                )

        # 4) Preview execution check:
        #    - If exec_result is provided, use it directly
        #    - Otherwise, if adapter has execute_preview, run it
        try:
            if exec_result is not None:
                er = exec_result
            elif adapter is not None and hasattr(adapter, "execute_preview"):
                er = adapter.execute_preview(repaired_sql or sql)
            else:
                er = {"ok": True}

            ok_val = (
                isinstance(er, dict) and isinstance(er.get("ok"), bool) and er["ok"]
            )
            if not ok_val:
                msg = None
                if isinstance(er, dict):
                    for k in ("error", "message", "detail"):
                        if k in er and er[k]:
                            msg = str(er[k])
                            break
                verifier_failures_total.labels(reason="preview_exec_error").inc()
                issues.append(f"exec_error:{msg or 'preview_failed'}")
        except Exception as e:
            verifier_failures_total.labels(reason="preview_exec_error").inc()
            issues.append(f"exec_exception:{e!s}")

        # 5) Final result and trace
        is_ok: bool = (not issues) or all(i.startswith("note:") for i in issues)
        ok_label: str = "true" if is_ok else "false"
        verifier_checks_total.labels(ok=ok_label).inc()

        if is_ok:
            data: Dict[str, Any] = {"verified": True}
            if repaired_sql:
                data["sql"] = repaired_sql
            return StageResult(
                ok=True,
                data=data,
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )
        else:
            return StageResult(
                ok=False,
                error=[i for i in issues if not i.startswith("note:")],
                trace=StageTrace(
                    stage=self.name, duration_ms=_ms(t0), notes={"issues": issues}
                ),
            )

    # Public alias for backward compatibility
    def run(
        self, *, sql: str, exec_result: Any = None, adapter: Any = None
    ) -> StageResult:
        """Back-compat wrapper around verify()."""
        return self.verify(sql, exec_result=exec_result, adapter=adapter)
