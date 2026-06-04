"""Deterministic JSON writers: stable key order, stable layout, clean git diffs."""
from __future__ import annotations

import json
from pathlib import Path


def dumps_stable(obj) -> str:
    """Pretty JSON with sorted keys - for small documents (meta, rankings)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def dumps_records(records: list) -> str:
    """JSON array with one compact object per line.

    Line-per-record keeps multi-megabyte lists diff-friendly: a weekly refresh
    that appends meetings touches only the new lines.
    """
    if not records:
        return "[]\n"
    lines = ",\n".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for r in records
    )
    return "[\n" + lines + "\n]\n"


def write_json(path: Path, obj, *, records: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_records(obj) if records else dumps_stable(obj)
    path.write_text(text, encoding="utf-8")


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
