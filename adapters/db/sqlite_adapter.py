import sqlite3
from typing import List, Tuple, Any
from adapters.db.base import DBAdapter

class SQLiteAdapter(DBAdapter):
    name = "sqlite"
    dialect = "sqlite"

    def __init__(self, path: str):
        self.path = path

    def preview_schema(self, limit_per_table: int = 0) -> str:
        with sqlite3.connect(self.path, uri=True) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys = ON")
            tables = [t[0] for t in cur.fetchall()]
            lines = []
            for t in tables:
                cur.execute(f"PRAGMA table_info({t});")
                cols = [f"{c[1]}:{c[2]}" for c in cur.fetchall()]
                lines.append(f"- {t} ({', '.join(cols)})")
            return "\n".join(lines)

    def execute(self, sql: str) -> Tuple[List[Tuple[Any, ...]], List[str]]:
        # enforce read-only connection
        uri = f"file:{self.path}?mode=ro&uri=true"
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            return rows, cols
