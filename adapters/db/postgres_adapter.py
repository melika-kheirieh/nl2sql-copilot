import psycopg
from typing import Any, List, Tuple
from adapters.db.base import DBAdapter

class PostgresAdapter(DBAdapter):
    name = "postgres"
    dialect = "postgres"

    def __init__(self, dsn: str):
        """
        DSN example:
        "dbname=demo user=postgres password=postgres host=localhost port=5432"
        """
        self.dsn = dsn

    def preview_schema(self, limit_per_table: int = 0) -> str:
        with psycopg.connect(self.dsn) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public';
            """)
            tables = [t[0] for t in cur.fetchall()]
            lines = []
            for t in tables:
                cur.execute(f"""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = %s;
                """, (t,))
                cols = [f"{c[0]}:{c[1]}" for c in cur.fetchall()]
                lines.append(f"- {t} ({', '.join(cols)})")
            return "\n".join(lines)

    def execute(self, sql: str) -> Tuple[List[Tuple[Any, ...]], List[str]]:
        if not sql.strip().lower().startswith("select"):
            raise ValueError("Only SELECT statements are allowed.")
        with psycopg.connect(self.dsn) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            return rows, cols
