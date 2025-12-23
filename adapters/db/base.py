from typing import Tuple, List, Any, Protocol


class DBAdapter(Protocol):
    """Abstract database adapter for read-only queries."""

    name: str
    dialect: str

    def preview_schema(self, limit_per_table: int = 0) -> str:
        """Human-friendly schema preview (may include types, bullets, samples)."""

    def derive_schema_preview(self) -> str:
        """LLM/eval schema preview. Format: table(col1, col2, ...) one per line."""

    def execute(self, sql: str) -> Tuple[List[Tuple[Any, ...]], List[str]]:
        """Execute a SELECT query and return (rows, columns)."""

    def explain_query_plan(self, sql: str) -> List[str]:
        """Return a query plan preview (must be read-only). Raise on failure."""
