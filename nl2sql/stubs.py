from nl2sql.types import StageResult, StageTrace

class NoOpExecutor:
    name = "executor"
    def run(self, sql: str) -> StageResult:
        # pretend success, return empty result set
        return StageResult(
            ok=True,
            data={"rows": [], "columns": []},
            trace=StageTrace(stage=self.name, duration_ms=0.0, notes={"noop": True})
        )

class NoOpVerifier:
    name = "verifier"
    def run(self, sql: str, exec_result: StageResult) -> StageResult:
        # always verified for legacy tests
        return StageResult(
            ok=True,
            data={"verified": True},
            trace=StageTrace(stage=self.name, duration_ms=0.0, notes={"noop": True})
        )

class NoOpRepair:
    name = "repair"
    def run(self, sql: str, error_msg: str, schema_preview: str) -> StageResult:
        # return original SQL unchanged
        return StageResult(
            ok=True,
            data={"sql": sql},
            trace=StageTrace(stage=self.name, duration_ms=0.0, notes={"noop": True})
        )
