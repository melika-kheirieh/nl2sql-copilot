from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional
from pathlib import Path

from nl2sql.pipeline import FinalResult
from nl2sql.pipeline_factory import pipeline_from_config_with_adapter
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter
from adapters.metrics.prometheus import PrometheusMetrics

from app import state
from app.settings import Settings
from app.errors import (
    AppError,
    DbNotFound,
    SchemaRequired,
    SchemaDeriveError,
    PipelineConfigError,
    PipelineRunError,
)

Adapter = Any  # You can replace this with a Protocol later


@dataclass
class NL2SQLService:
    """
    Application-level service for the NL2SQL use-case.

    Responsibilities:
        - Choose the right DB adapter based on db_mode + db_id.
        - Derive or accept schema preview.
        - Build and run the pipeline for a given query.
    """

    settings: Settings

    def _select_adapter(self, db_id: Optional[str]) -> Adapter:
        mode = self.settings.db_mode.lower()

        if mode == "postgres":
            dsn = (self.settings.postgres_dsn or "").strip()
            if not dsn:
                raise PipelineConfigError("Postgres DSN is not configured")
            return PostgresAdapter(dsn=dsn)

        if db_id:
            state.cleanup_stale_dbs()
            path = state.get_db_path(db_id)
            if not path:
                raise DbNotFound(f"Could not resolve DB for db_id={db_id!r}")
            return SQLiteAdapter(path=path)

        default_path = self.settings.default_sqlite_path
        if not Path(default_path).exists():
            raise DbNotFound(f"SQLite database path does not exist: {default_path!r}")

        return SQLiteAdapter(path=default_path)

    def _introspect_sqlite_schema(self, adapter: Adapter) -> str:
        """
        Build a lightweight textual schema preview for a SQLite database.

        This is a straight port of the previous sqlite3 logic, but contained
        inside the service instead of the router.
        """
        db_path = getattr(adapter, "db_path", None) or getattr(adapter, "path", None)
        if not db_path:
            raise RuntimeError(
                "SQLite adapter must expose a .db_path or .path attribute"
            )

        if not Path(db_path).exists():
            raise FileNotFoundError(f"SQLite database path does not exist: {db_path}")

        lines: list[str] = []
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            tables = [row[0] for row in cur.fetchall()]

            for table in tables:
                cur.execute(f"PRAGMA table_info({table})")
                cols = [row[1] for row in cur.fetchall()]
                if cols:
                    lines.append(f"{table}({', '.join(cols)})")
        finally:
            conn.close()

        return "\n".join(lines)

    def get_schema_preview(
        self,
        db_id: Optional[str],
        override: Optional[str],
    ) -> str:
        """
        Decide which schema preview to use.

        - If override is provided by the client → use it.
        - Else, in sqlite mode → introspect the DB.
        - In postgres mode without override → fail fast.
        """
        if override:
            return override

        mode = self.settings.db_mode.lower()
        if mode == "postgres":
            raise SchemaRequired("schema_preview is required in postgres mode")

        try:
            adapter = self._select_adapter(db_id)
            return self._introspect_sqlite_schema(adapter)
        except DbNotFound:
            raise
        except Exception as exc:
            raise SchemaDeriveError("failed to derive schema preview") from exc

    def run_query(
        self,
        *,
        query: str,
        db_id: Optional[str],
        schema_preview: str,
    ) -> FinalResult:
        """Build a pipeline for the given DB and run the query through it."""
        try:
            adapter = self._select_adapter(db_id)
        except AppError:
            raise
        except Exception as exc:
            raise PipelineRunError("failed to select adapter") from exc

        try:
            pipeline = pipeline_from_config_with_adapter(
                self.settings.pipeline_config_path,
                adapter=adapter,
            )
        except FileNotFoundError as exc:
            raise PipelineConfigError(
                f"Pipeline config not found at {self.settings.pipeline_config_path!r}"
            ) from exc
        except Exception as exc:
            raise PipelineConfigError(
                f"Failed to build pipeline from {self.settings.pipeline_config_path!r}: {exc}"
            ) from exc

        # Force PrometheusMetrics to avoid silent NoOp wiring via factory defaults.
        if (
            getattr(pipeline, "metrics", None) is None
            or pipeline.metrics.__class__.__name__ == "NoOpMetrics"
        ):
            pipeline.metrics = PrometheusMetrics()

        try:
            result = pipeline.run(user_query=query, schema_preview=schema_preview)
        except AppError:
            raise
        except Exception as exc:
            raise PipelineRunError("pipeline crashed during execution") from exc

        return result
