from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from nl2sql.safety import Safety
from nl2sql.verifier import Verifier

# pick adapter for verifier (SQLite default)
from adapters.db.sqlite_adapter import SQLiteAdapter

from dataclasses import is_dataclass, asdict
from typing import Any


def _is_dataclass_instance(x: Any) -> bool:
    # True only for dataclass *instances* (not classes)
    return is_dataclass(x) and not isinstance(x, type)


def _to_dict(obj: Any) -> dict:
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return obj.model_dump()  # type: ignore[no-any-return]
    # Pydantic v1
    if hasattr(obj, "dict"):
        return obj.dict()  # type: ignore[no-any-return]
    # Dataclass instance
    if _is_dataclass_instance(obj):
        return asdict(obj)  # type: ignore[arg-type]
    # Plain object
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {"value": str(obj)}


router = APIRouter(prefix="/_dev", tags=["dev"])


class SQLBody(BaseModel):
    sql: str


@router.post("/safety")
def dev_safety_check(body: SQLBody):
    """
    Run the Safety stage directly on a raw SQL string.
    Used for metrics validation (Prometheus counters).
    """
    s = Safety()
    res = s.check(body.sql)
    return _to_dict(res)


@router.post("/verifier")
def dev_verifier_check(body: SQLBody):
    """
    Run the Verifier stage directly on a raw SQL string
    with a real adapter connection.
    """
    try:
        adapter = SQLiteAdapter("data/chinook.db")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Adapter init failed: {e}")

    v = Verifier()
    res = v.verify(body.sql, adapter=adapter)
    return _to_dict(res)
