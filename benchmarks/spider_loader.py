from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class SpiderItem:
    db_id: str
    question: str
    gold_sql: str
    db_path: str  # absolute path to the sqlite file


# ---------- helpers ----------


def _candidate_roots(env_root: Optional[str]) -> List[Path]:
    """
    Build a small list of candidate Spider roots to tolerate common layouts:
    - $SPIDER_ROOT
    - data/spider
    - data/spider/spider        (when the repo was cloned into data/spider/spider)
    - <env>/spider              (when SPIDER_ROOT points to the parent directory)
    """
    cands: List[Path] = []
    if env_root:
        p = Path(env_root).expanduser().resolve()
        cands.append(p)
        cands.append((p / "spider").resolve())
    # project-local defaults
    here = Path.cwd().resolve()
    cands.append((here / "data" / "spider").resolve())
    cands.append((here / "data" / "spider" / "spider").resolve())
    # de-dup
    seen, uniq = set(), []
    for x in cands:
        if str(x) not in seen:
            uniq.append(x)
            seen.add(str(x))
    return uniq


def _resolve_split_json(root: Path, split: str) -> Path:
    """
    Map split name to file name and return full path under `root`.
    Spider uses:
      - dev.json
      - train_spider.json
    """
    fname = "dev.json" if split == "dev" else "train_spider.json"
    return (root / fname).resolve()


def _resolve_database_dir(root: Path) -> Path:
    return (root / "database").resolve()


def _ensure_exists(path: Path, kind: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{kind} not found: {path}")


# ---------- public API ----------


def load_spider_sqlite(
    *, split: str = "dev", limit: Optional[int] = None
) -> List[SpiderItem]:
    """
    Load a subset of Spider (dev/train) and attach absolute sqlite db paths.
    Looks under:
      - $SPIDER_ROOT (if set)
      - ./data/spider
      - ./data/spider/spider
      - $SPIDER_ROOT/spider
    """
    env_root = os.getenv("SPIDER_ROOT")
    roots = _candidate_roots(env_root)

    # find a root that actually contains the split file & database/
    json_path: Optional[Path] = None
    database_dir: Optional[Path] = None
    chosen_root: Optional[Path] = None

    for r in roots:
        jp = _resolve_split_json(r, split)
        dbd = _resolve_database_dir(r)
        if jp.exists() and dbd.exists():
            json_path, database_dir, chosen_root = jp, dbd, r
            break

    if json_path is None or database_dir is None:
        debug = "\n".join(
            f"- {str(_resolve_split_json(r, split))}  |  {str(_resolve_database_dir(r))}"
            for r in roots
        )
        raise RuntimeError(
            "Failed to locate Spider dataset.\n"
            f"Checked candidates for split='{split}':\n{debug}\n"
            "Tip: export SPIDER_ROOT=/absolute/path/to/spider  "
            "(the folder that directly contains dev.json/train_spider.json and database/)"
        )

    # read split
    try:
        items = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to read Spider split file: {json_path} ({e})")

    # build rows with absolute sqlite path
    out: List[SpiderItem] = []
    for obj in items:
        db_id: str = obj.get("db_id", "")
        q: str = obj.get("question", "").strip()
        gold: str = obj.get("query", obj.get("sql", "")).strip()  # Spider uses 'query'
        if not (db_id and q and gold):
            continue

        # <root>/database/<db_id>/<db_id>.sqlite
        db_file = (database_dir / db_id / f"{db_id}.sqlite").resolve()
        if not db_file.exists():
            # some mirrors use .db ; try a fallback
            alt = (database_dir / db_id / f"{db_id}.db").resolve()
            if alt.exists():
                db_file = alt
            else:
                # skip if DB file missing
                # (you could also raise here if you prefer strict behavior)
                continue

        out.append(
            SpiderItem(db_id=db_id, question=q, gold_sql=gold, db_path=str(db_file))
        )

        if limit is not None and len(out) >= limit:
            break

    if not out:
        raise RuntimeError(
            f"No usable items from {json_path} (limit={limit}). "
            "Check db files under database/<db_id>/<db_id>.sqlite"
        )

    # small info for sanity
    print(
        f"✔ Spider root: {chosen_root}\n"
        f"✔ Split file:  {json_path.name} ({len(out)} items)"
    )
    return out


def open_readonly_connection(db_path: str) -> sqlite3.Connection:
    """
    Open SQLite in read-only mode (URI).
    """
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)
