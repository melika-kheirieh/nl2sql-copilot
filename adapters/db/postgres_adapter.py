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
        """
        Return a simple textual preview of tables and their columns in public schema.
        Example line: "- users (id:integer, name:text)"
        """
        lines: List[str] = []
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                # list tables
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    ORDER BY table_name;
                    """
                )
                table_rows = cur.fetchall() or []
                tables: List[str] = [t[0] for t in table_rows if t and t[0]]

                for t in tables:
                    # list columns for table t
                    cur.execute(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = %s
                        ORDER BY ordinal_position;
                        """,
                        (t,),
                    )
                    col_rows = cur.fetchall() or []
                    # guard against None; build "name:type"
                    cols: List[str] = [
                        f"{c[0]}:{c[1]}" for c in col_rows if c and len(c) >= 2
                    ]
                    lines.append(f"- {t} ({', '.join(cols)})")

        return "\n".join(lines)

    def execute(self, sql: str) -> Tuple[List[Tuple[Any, ...]], List[str]]:
        """
        Execute a read-only SELECT query and return (rows, columns).
        """
        if not sql or not sql.strip().lower().startswith("select"):
            raise ValueError("Only SELECT statements are allowed.")

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall() or []
                desc = cur.description or ()
                cols: List[str] = [d[0] for d in desc if d]
                return rows, cols
