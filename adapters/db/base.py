from typing import Tuple, List, Any, Protocol


class DBAdapter(Protocol):
    """Abstract database adapter for read-only queries."""

    name: str
    dialect: str

    def preview_schema(self, limit_per_table: int = 0) -> str:
        """Generate a readable summary of the database schema with optional sample rows per table."""

    def execute(self, sql: str) -> Tuple[List[Tuple[Any, ...]], List[str]]:
        """Execute a SELECT query and return (rows, columns)."""
