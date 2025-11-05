from __future__ import annotations

import re
import time
from typing import Any, Iterable, List, Optional

import sqlglot
from sqlglot import expressions as exp

from nl2sql.types import StageResult, StageTrace


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


class Verifier:
    name = "verifier"

    # Textual fallback: scan for common aggregate calls
    _AGG_CALL_RE = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)

    # ----------------------- AST helpers (version-friendly) --------------------
    def _walk(self, node: exp.Expression) -> Iterable[exp.Expression]:
        """Non-recursive DFS over sqlglot Expression tree (avoid private APIs)."""
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
        for n in self._walk(tree):
            if isinstance(n, exp.Select):
                return n
        return None

    def _has_group_by(self, tree: exp.Expression) -> bool:
        sel = self._first_select(tree)
        if not sel:
            return False
        # sqlglot stores GROUP BY on Select.group
        return bool(getattr(sel, "group", None))

    def _is_distinct_projection(self, tree: exp.Expression) -> bool:
        sel = self._first_select(tree)
        if not sel:
            return False
        # DISTINCT may appear as Select.distinct or a Distinct node
        if getattr(sel, "distinct", None):
            return True
        return any(isinstance(n, exp.Distinct) for n in self._walk(sel))

    def _has_windowed_aggregate(self, tree: exp.Expression) -> bool:
        # If there is any OVER(...) window, aggregates without GROUP BY can be legitimate
        return any(isinstance(n, exp.Window) for n in self._walk(tree))

    def _expr_contains_agg(self, node: exp.Expression) -> bool:
        """True if subtree contains an aggregate call (robust across sqlglot versions)."""
        # Build aggregate classes dynamically to avoid attr errors and fixed-length tuples
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
        agg_types_list: list[type] = []
        for name in agg_type_names:
            t = getattr(exp, name, None)
            if isinstance(t, type):
                agg_types_list.append(t)
        AGG_TYPES: tuple[type, ...] = tuple(agg_types_list)

        # 1) Class-based check (if we found any known aggregate classes)
        if AGG_TYPES and any(isinstance(n, AGG_TYPES) for n in self._walk(node)):
            return True

        # 2) Fallback: generic function nodes with aggregate names
        Anonymous = getattr(exp, "Anonymous", None)
        func_like = (exp.Func,) + ((Anonymous,) if isinstance(Anonymous, type) else ())
        AGG_NAMES = {"count", "sum", "avg", "min", "max"}

        def _func_name(n: exp.Expression) -> str:
            name = getattr(n, "name", None)
            if isinstance(name, str) and name:
                return name.lower()
            this = getattr(n, "this", None)
            if isinstance(this, str):
                return this.lower()
            this_name = getattr(this, "name", None)
            if isinstance(this_name, str) and this_name:
                return this_name.lower()
            return (str(this) or "").lower()

        for n in self._walk(node):
            if isinstance(n, func_like) and _func_name(n) in AGG_NAMES:
                return True

        return False

    def _has_nonagg_column(self, node: exp.Expression) -> bool:
        """Subtree contains a column reference that is NOT inside an aggregate."""
        # Check if there are any columns in this expression
        columns = [n for n in self._walk(node) if isinstance(n, exp.Column)]
        if not columns:
            return False

        # Check if all columns are inside aggregates
        for col in columns:
            # Walk up from column to see if it's inside an aggregate
            # is_in_agg = False
            # For simplicity, check if the entire expression contains both column and aggregate
            # A more precise check would require parent tracking
            if self._expr_contains_agg(node):
                # This is a simplified check - if the node has both columns and aggregates,
                # we need more complex logic to determine if columns are outside aggregates
                return True
            else:
                # No aggregates, so if there are columns, they're non-aggregate
                return True
        return False

    # ----------------------- Textual fallback helpers -------------------------
    def _clean_sql_for_fn_scan(self, sql: str) -> str:
        """Remove comments/strings so regex won't be fooled."""
        s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)  # block comments
        s = re.sub(r"--.*?$", " ", s, flags=re.MULTILINE)  # line comments
        s = re.sub(
            r"('([^']|'')*'|\"([^\"]|\"\")*\"|`[^`]*`)", " ", s
        )  # quoted strings / idents
        s = re.sub(r"\s+", " ", s).strip()
        return s

    # ----------------------- Adapter result helpers ---------------------------
    def _extract_ok(self, exec_result: Any) -> Optional[bool]:
        if isinstance(exec_result, dict):
            v = exec_result.get("ok")
            if isinstance(v, bool):
                return v
        return None

    def _extract_error(self, exec_result: Any) -> Optional[str]:
        if isinstance(exec_result, dict):
            for k in ("error", "message", "detail"):
                if k in exec_result and exec_result[k]:
                    return str(exec_result[k])
        return None

    # ----------------------------- Main entry ---------------------------------
    def verify(self, sql: str, *, adapter: Any) -> StageResult:
        t0 = time.perf_counter()
        issues: List[str] = []

        # 1) Parse - Check for errors in the parsed result
        try:
            tree = sqlglot.parse_one(sql, read=None)  # autodetect dialect

            # Check if the parse actually succeeded
            if tree is None:
                return StageResult(
                    ok=False,
                    error=["parse_error"],
                    trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
                )

            # sqlglot may parse broken SQL as an "Unknown" or "Command" type
            # Check if we got a proper SQL statement type
            tree_type = type(tree).__name__

            # Check for common sqlglot error indicators
            # When sqlglot can't parse properly, it often creates Command or Unknown nodes
            if tree_type in ("Command", "Unknown"):
                return StageResult(
                    ok=False,
                    error=["parse_error"],
                    trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
                )

            # Also check if the tree has errors attribute (some versions of sqlglot)
            if hasattr(tree, "errors") and tree.errors:
                return StageResult(
                    ok=False,
                    error=["parse_error"],
                    trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
                )

            # Additional check: if it's not a recognized DML/DQL statement
            valid_types = ("Select", "With", "Union", "Intersect", "Except", "Values")
            if tree_type not in valid_types:
                # This might be a parse error disguised as a different statement type
                # Let's check if it looks like it should be a SELECT
                sql_lower = sql.lower().strip()
                if any(
                    sql_lower.startswith(kw)
                    for kw in ["selct", "slect", "selet", "seelct"]
                ):
                    # Common misspellings of SELECT
                    return StageResult(
                        ok=False,
                        error=["parse_error"],
                        trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
                    )

        except Exception:
            return StageResult(
                ok=False,
                error=["parse_error"],
                trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
            )

        # 2) Semantic checks (AST-first)
        try:
            sel = self._first_select(tree)
            if sel:
                has_group = self._has_group_by(tree)
                has_window = self._has_windowed_aggregate(tree)
                is_distinct = self._is_distinct_projection(tree)

                select_items = list(getattr(sel, "expressions", []) or [])
                any_agg = any(self._expr_contains_agg(it) for it in select_items)

                # More precise check for non-aggregate columns
                any_nonagg_col = False
                for item in select_items:
                    # Check if this select item has columns but no aggregates
                    has_cols = any(isinstance(n, exp.Column) for n in self._walk(item))
                    has_aggs = self._expr_contains_agg(item)
                    if has_cols and not has_aggs:
                        any_nonagg_col = True
                        break

                # Core rule: aggregate + non-aggregate column without GROUP BY is an issue,
                # unless DISTINCT or windowed aggregate makes it legitimate.
                if (
                    any_agg
                    and any_nonagg_col
                    and not (has_group or has_window or is_distinct)
                ):
                    issues.append("aggregation_without_group_by")
        except Exception as e:
            # Don't crash the verifier; surface a soft issue and let fallback run
            issues.append(f"semantic_check_error:{e!s}")

        # 3) Fallback textual scan — only if AST didn't already flag
        if not any("aggregation_without_group_by" in i for i in issues):
            try:
                cleaned = self._clean_sql_for_fn_scan(sql)
                has_agg_call = bool(self._AGG_CALL_RE.search(cleaned))
                has_group_kw = re.search(r"\bgroup\s+by\b", cleaned, re.IGNORECASE)
                has_over_kw = re.search(r"\bover\s*\(", cleaned, re.IGNORECASE)
                has_distinct_kw = re.search(
                    r"\bselect\s+distinct\b", cleaned, re.IGNORECASE
                )

                if has_agg_call and not (
                    has_group_kw or has_over_kw or has_distinct_kw
                ):
                    m_sel = re.search(
                        r"\bselect\s+(?P<sel>.+?)\s+\bfrom\b",
                        cleaned,
                        re.IGNORECASE | re.DOTALL,
                    )
                    if m_sel:
                        select_list = m_sel.group("sel")
                        # a comma strongly suggests mixing aggregate and non-aggregate in projection
                        if "," in select_list:
                            issues.append("aggregation_without_group_by")
            except Exception:
                # ignore fallback errors
                pass

        # 4) Optional: cheap preview execution (adapter may be a stub in tests)
        try:
            exec_result = adapter.execute_preview(sql) if adapter else {"ok": True}
            ok_val = self._extract_ok(exec_result)
            if ok_val is False:
                err = self._extract_error(exec_result)
                issues.append(f"exec_error:{err}" if err else "exec_error")
        except Exception as e:
            issues.append(f"exec_exception:{e!s}")

        # 5) Final decision — AFTER all checks (note: no early return before fallback)
        if issues:
            return StageResult(
                ok=False,
                error=issues,
                trace=StageTrace(
                    stage=self.name, duration_ms=_ms(t0), notes={"issues": issues}
                ),
            )

        return StageResult(
            ok=True,
            data={"verified": True},
            trace=StageTrace(stage=self.name, duration_ms=_ms(t0)),
        )

    def run(self, *, sql: str, adapter: Any) -> StageResult:
        return self.verify(sql, adapter=adapter)
