## Stage: detector

- Input: user_query (str) + schema_preview (str|None)
- Output: questions (list[str]) for clarification (if any)
- Failure signals: questions_len > 0 (treated as "ambiguous" early-exit) OR exception in detect()
- Next action: if ambiguous -> return FinalResult(ambiguous=True); else continue to planner


## Stage: planner

- Input: user_query + schema_for_llm (budgeted schema_preview) + traces
- Output: dict { plan: str, used_tables: list[str], usage: {...} } (wrapped into StageResult by _safe_stage)
- Failure signals: exception / StageResult.ok == False
- Next action: attempt repair once (via repair stage); if still fails -> return pipeline error (LLM/planner failure)


## Stage: generator

- Input: user_query + schema_for_llm + plan_text + clarify_answers + constraints + traces
- Output: dict { sql: str, rationale: str, ... } (wrapped into StageResult by _safe_stage)
- Failure signals: exception / StageResult.ok == False OR empty/blank sql after generation
- Next action: attempt repair once; if still fails -> return pipeline error (LLM bad output)


## Stage: safety

- Input: sql (str)
- Output: StageResult(ok=True, data={ sql: sanitized_sql, original_len, sanitized_len, allow_explain })
- Failure signals: empty_sql / sql_too_long / non_select root_type / forbidden_ast / explain_not_allowed
- Next action: abort (NO repair for safety blocks; pipeline stops with safety failure)


## Stage: executor (DB sandbox)

- Input: sql (str) [already passed safety]
- Output: StageResult(ok=True, data={ rows: list, columns: list }, trace includes row_count/col_count/preflight notes)
- Failure signals:
  - preflight cost check fails -> ok=False, error_code=EXECUTOR_COST_GUARDRAIL_BLOCKED (retryable=False)
  - db.execute raises exception -> ok=False with error text
- Next action:
  - if blocked_by_cost -> abort (NO repair)
  - if SQL error seems repairable -> attempt repair once then re-run executor
  - otherwise -> return execution error


## Stage: verifier

- Input: sql + exec_result {rows, columns} + schema_preview + traces
- Output: StageResult with data including verified: bool (+ optional reason/notes)
- Failure signals: verified == False (semantic failure) OR StageResult.ok == False / exception
- Next action: attempt repair once (semantic_failure OR repairable SQL error); if still fails -> return verifier failure


## Stage: repair

- Input: sql + error_msg + schema_preview  (builders differ per stage)
- Output: StageResult(ok=True, data={ sql: fixed_sql })
- Failure signals: StageResult.ok == False / exception in repair
- Next action: if repair succeeds -> re-run the failed stage once; if repair fails -> stop and return original stage failure
