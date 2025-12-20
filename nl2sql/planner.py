from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple, Optional

__all__ = ["Planner"]


def _extract_table_names_from_schema(schema_text: str) -> List[str]:
    """Best-effort table name extraction from schema preview."""
    if not schema_text:
        return []
    names = re.findall(
        r"(?im)^\s*create\s+table\s+`?([A-Za-z_][A-Za-z0-9_]*)`?\b", schema_text
    )
    # de-dup preserving order
    seen: set[str] = set()
    out: List[str] = []
    for n in names:
        if n not in seen:
            out.append(n)
            seen.add(n)
    return out


# --------- Heuristic schema trimming (safe, mypy-clean) ---------
def _tokenize_lower(s: str) -> List[str]:
    return re.findall(r"[a-z_]+", (s or "").lower())


def _table_blocks(schema_text: str) -> List[Tuple[str, List[str]]]:
    """
    Parse plain-text schema into [(table_name, lines)] blocks,
    assuming SQLite preview format like:
      Table: users
        - id
        - name
    """
    blocks: List[Tuple[str, List[str]]] = []
    cur_name: Optional[str] = None
    cur_lines: List[str] = []

    def _flush():
        nonlocal cur_name, cur_lines
        if cur_name is not None:
            blocks.append((cur_name, cur_lines))
        cur_name, cur_lines = None, []

    for raw in (schema_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^table:\s*([a-zA-Z0-9_]+)\s*$", line, re.IGNORECASE)
        if m:
            _flush()
            cur_name = m.group(1)
            cur_lines = [raw]
        else:
            if cur_name is not None:
                cur_lines.append(raw)

    _flush()
    return blocks


def _pick_relevant_tables(schema_text: str, question: str, k: int = 3) -> str:
    """Keep up to k tables with highest lexical overlap with the question."""
    try:
        blocks = _table_blocks(schema_text)
        if not blocks:
            return schema_text

        q_toks = set(_tokenize_lower(question))
        scored: List[Tuple[int, str, List[str]]] = []
        for name, lines in blocks:
            score = sum(1 for tok in _tokenize_lower(" ".join(lines)) if tok in q_toks)
            scored.append((score, name, lines))

        scored.sort(key=lambda x: (-x[0], x[1]))
        top = scored[:k]
        # Keep stable order by original appearance? We'll keep by score then name for determinism.
        out_lines: List[str] = []
        for _, _, lines in top:
            out_lines.extend(lines)
            out_lines.append("")  # spacing

        return "\n".join(out_lines).strip() if out_lines else schema_text
    except Exception:
        return schema_text


class Planner:
    """Planner wrapper around the LLM provider."""

    def __init__(self, *, llm, model_id: str | None = None) -> None:
        self.llm = llm
        # ensure model_id is always a str (for mypy)
        self.model_id: str = str(model_id or getattr(llm, "model", "unknown"))
        # in-memory cache: (model, hash(q), hash(trimmed)) → (plan, pin, pout, cost)
        self._plan_cache: dict[
            tuple[str, int, int], tuple[str, List[str], int, int, float]
        ] = {}

    def run(
        self,
        *,
        user_query: str,
        schema_preview: str,
        constraints: Optional[List[str]] = None,
        traces: Optional[List[dict]] = None,
    ) -> Dict[str, Any]:
        """Plan the query. Assumes schema_preview is already budgeted upstream."""
        schema_preview = schema_preview or ""
        constraints = constraints or []

        key: tuple[str, int, int] = (
            self.model_id,
            hash(user_query or ""),
            hash(schema_preview),
        )

        if key in self._plan_cache:
            plan_text, used_tables, pin, pout, cost = self._plan_cache[key]
        else:
            # Call provider with backward-compatible kwargs
            try:
                res = self.llm.plan(
                    user_query=user_query,
                    schema_preview=schema_preview,
                    constraints=constraints,
                )
            except TypeError:
                # Older fakes/providers may not accept `constraints`
                res = self.llm.plan(
                    user_query=user_query,
                    schema_preview=schema_preview,
                )

            if not isinstance(res, tuple):
                raise TypeError("LLM plan() must return a tuple")

            if len(res) == 5:
                plan_text, used_tables, pin, pout, cost = res
            elif len(res) == 4:
                plan_text, pin, pout, cost = res
                used_tables = _extract_table_names_from_schema(schema_preview)
            else:
                raise TypeError("LLM plan() must return 4- or 5-tuple")

            # Ensure used_tables is always a list[str]
            if not isinstance(used_tables, list):
                used_tables = _extract_table_names_from_schema(schema_preview)

            self._plan_cache[key] = (plan_text, used_tables, pin, pout, cost)

        return {
            "plan": plan_text,
            "used_tables": used_tables,
            "usage": {
                "prompt_tokens": pin,
                "completion_tokens": pout,
                "cost_usd": cost,
            },
        }
