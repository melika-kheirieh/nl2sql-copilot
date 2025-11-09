from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple, Optional

__all__ = ["Planner"]


# --------- Heuristic schema trimming (safe, mypy-clean) ---------
def _tokenize_lower(s: str) -> List[str]:
    return re.findall(r"[a-z_]+", (s or "").lower())


def _table_blocks(schema_text: str) -> List[Tuple[str, List[str]]]:
    """
    Parse plain-text schema into [(table_name, lines)] blocks,
    supporting both 'Table: name' and 'CREATE TABLE name (' styles.
    """
    blocks: List[Tuple[str, List[str]]] = []
    cur_name: Optional[str] = None
    cur_lines: List[str] = []

    def _flush() -> None:
        nonlocal cur_name, cur_lines
        if cur_name is not None and cur_lines:
            blocks.append((cur_name, cur_lines[:]))
        cur_name, cur_lines = None, []

    for line in (schema_text or "").splitlines():
        m = re.search(r"Table:\s*(\w+)", line, flags=re.IGNORECASE)
        m2 = re.search(r"CREATE\s+TABLE\s+(\w+)\s*\(", line, flags=re.IGNORECASE)

        started = False
        name: Optional[str] = None
        if m is not None:
            name = m.group(1)
            started = True
        elif m2 is not None:
            name = m2.group(1)
            started = True

        if started and name:
            _flush()
            cur_name = name
            cur_lines.append(line)
        else:
            if cur_name is not None:
                cur_lines.append(line)

        if cur_name is not None and line.strip().endswith(");"):
            _flush()

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
            score = sum(1 for w in _tokenize_lower(name) if w in q_toks)
            cols_line = " ".join(lines)
            cols = re.findall(r"\b([A-Za-z_]\w*)\b", cols_line)
            score += min(2, sum(1 for c in cols if c.lower() in q_toks))
            scored.append((score, name, lines))

        scored.sort(key=lambda t: t[0], reverse=True)
        keep = [b for b in scored[: max(1, k)] if b[0] > 0]
        if not keep:
            keep = scored[: max(1, k)]

        out_lines: List[str] = []
        for _, _, lines in keep:
            out_lines.extend(lines)
            if lines and lines[-1].strip() != "":
                out_lines.append("")
        trimmed = "\n".join(out_lines).strip()
        return trimmed if trimmed else schema_text
    except Exception:
        return schema_text


# ------------------------------ Planner ------------------------------
class Planner:
    """Planner wrapper around the LLM provider."""

    def __init__(self, *, llm, model_id: str | None = None) -> None:
        self.llm = llm
        # ensure model_id is always a str (for mypy)
        self.model_id: str = str(model_id or getattr(llm, "model", "unknown"))
        # in-memory cache: (model, hash(q), hash(trimmed)) → (plan, pin, pout, cost)
        self._plan_cache: dict[tuple[str, int, int], tuple[str, int, int, float]] = {}

    def run(self, *, user_query: str, schema_preview: str) -> Dict[str, Any]:
        trimmed = _pick_relevant_tables(schema_preview or "", user_query or "", k=3)

        key: tuple[str, int, int] = (
            self.model_id,
            hash(user_query or ""),
            hash(trimmed),
        )
        if key in self._plan_cache:
            plan_text, pin, pout, cost = self._plan_cache[key]
        else:
            plan_text, pin, pout, cost = self.llm.plan(
                user_query=user_query, schema_preview=trimmed
            )
            self._plan_cache[key] = (plan_text, pin, pout, cost)

        return {
            "plan": plan_text,
            "usage": {
                "prompt_tokens": pin,
                "completion_tokens": pout,
                "cost_usd": cost,
            },
        }
