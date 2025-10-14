from __future__ import annotations
import json, pathlib, sqlite3
from dataclasses import dataclass
from typing import List, Optional
import os

SPIDER_ROOT = pathlib.Path(
    os.getenv("SPIDER_ROOT", "data/spider")
)

@dataclass
class SpiderItem:
    db_id: str
    question: str
    gold_sql: str
    db_path: pathlib.Path

def load_spider_sqlite(split: str = "dev", limit: Optional[int] = None) -> List[SpiderItem]:
    fn = {"dev": "dev.json", "train": "train_spider.json"}[split]
    json_path = SPIDER_ROOT / fn
    try:
        items = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to read Spider split file: {json_path} ({e})")


    out: list[SpiderItem] = []
    for ex in items[: (limit or len(items))]:
        db_id = ex["db_id"]
        db_path = SPIDER_ROOT / "database" / db_id / f"{db_id}.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(f"Missing SQLite DB for {db_id}: {db_path}")
        out.append(
            SpiderItem(
                db_id=db_id,
                question=ex["question"],
                gold_sql=ex["query"],
                db_path=db_path
            )
        )
    return out


def open_readonly_connection(db_path: pathlib.Path, timeout: float=5.0) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro&uri=true"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn