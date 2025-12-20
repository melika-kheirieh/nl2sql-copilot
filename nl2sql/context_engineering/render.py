from __future__ import annotations

from .types import SchemaPack


def render_schema_pack(pack: SchemaPack) -> str:
    lines: list[str] = []
    for table in sorted(pack.tables.keys()):
        cols = pack.tables[table].columns
        if cols:
            lines.append(f"{table}({', '.join(cols)})")
        else:
            lines.append(f"{table}()")
    return "\n".join(lines)
