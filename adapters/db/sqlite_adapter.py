import sqlite3
import logging
from typing import List, Tuple, Any
from adapters.db.base import DBAdapter

log = logging.getLogger(__name__)


class SQLiteAdapter(DBAdapter):
    name = "sqlite"
    dialect = "sqlite"

    def __init__(self, path: str):
        self.path = path
        log.info("SQLiteAdapter initialized with DB path: %s", self.path)

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
        log.info("SQLiteAdapter opening read-only connection to: %s", uri)
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            cur = conn.cursor()
            log.debug("Executing SQL: %s", sql.strip().replace("\n", " "))
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            log.info("Query executed successfully. Returned %d rows.", len(rows))
            return rows, cols
