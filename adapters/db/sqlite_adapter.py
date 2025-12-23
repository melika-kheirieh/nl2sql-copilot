import sqlite3
import logging
from typing import List, Tuple, Any
from adapters.db.base import DBAdapter
from pathlib import Path

log = logging.getLogger(__name__)


class SQLiteAdapter(DBAdapter):
    name = "sqlite"
    dialect = "sqlite"

    def __init__(self, path: str):
        # resolve absolute path for safety
        self.path = Path(path).resolve()
        log.info("SQLiteAdapter initialized with DB path: %s", self.path)

    def preview_schema(self, limit_per_table: int = 0) -> str:
        if not self.path.exists():
            raise FileNotFoundError(f"SQLite DB does not exist: {self.path}")
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [t[0] for t in cur.fetchall()]
            lines = []
            for t in tables:
                cur.execute(f"PRAGMA table_info({t});")
                cols = [f"{c[1]}:{c[2]}" for c in cur.fetchall()]
                lines.append(f"- {t} ({', '.join(cols)})")
            return "\n".join(lines)

    def execute(self, sql: str) -> Tuple[List[Tuple[Any, ...]], List[str]]:
        if not self.path.exists():
            raise FileNotFoundError(f"SQLite DB does not exist: {self.path}")
        # use proper SQLite URI (not .as_uri())
        uri = f"file:{self.path}?mode=ro"
        log.info("SQLiteAdapter opening read-only connection to: %s", uri)
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            cur = conn.cursor()
            log.debug("Executing SQL: %s", sql.strip().replace("\n", " "))
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            log.info("Query executed successfully. Returned %d rows.", len(rows))
            return rows, cols

    def explain_query_plan(self, sql: str) -> List[str]:
        if not self.path.exists():
            raise FileNotFoundError(f"SQLite DB does not exist: {self.path}")

        sql_stripped = (sql or "").strip().rstrip(";")
        if not sql_stripped.lower().startswith("select"):
            raise ValueError("Only SELECT statements are allowed.")

        uri = f"file:{self.path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            # Extra safety: enforce query-only mode if available
            try:
                conn.execute("PRAGMA query_only = ON;")
            except Exception:
                pass
            cur = conn.execute(f"EXPLAIN QUERY PLAN {sql_stripped}")
            rows = cur.fetchall() or []
            # Rows are typically (id, parent, notused, detail)
            plan_lines: List[str] = [str(r[-1]) for r in rows if r]
            return plan_lines

    def derive_schema_preview(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError(f"SQLite DB does not exist: {self.path}")

        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name;"
            )
            tables = [t[0] for t in cur.fetchall() if t and t[0]]

            lines: list[str] = []
            for t in tables:
                cur.execute("PRAGMA table_info(?);", (t,))  # safer than f-string
                cols = [c[1] for c in cur.fetchall() if c and len(c) >= 2]
                if cols:
                    lines.append(f"{t}({', '.join(cols)})")

            return "\n".join(lines)
