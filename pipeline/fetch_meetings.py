"""Fetch declared MEP-lobbyist meetings from the official CSV export.

Source: the European Parliament "search MEP meetings" tool
(https://www.europarl.europa.eu/meps/en/search-meetings), which exports CSV.

The export caps every query at 1,000 rows, sorted by date DESCENDING - the
earliest rows of an over-full window are silently dropped (verified
empirically; pagination parameters are ignored). Date windows are therefore
bisected adaptively until each one fits under the cap. A single day that
still hits the cap cannot be subdivided and is reported loudly.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from collections import Counter
from datetime import date, timedelta
from typing import Iterator

from . import config, net, normalize

log = logging.getLogger(__name__)

EXPECTED_COLUMNS = {
    "title",
    "member_id",
    "member_name",
    "meeting_date",
    "member_capacity",
    "procedure_reference",
    "attendees",
    "lobbyist_id",
}

RAW_MEETINGS_DIR = config.RAW_DIR / "meetings"

# Transparency Register ids look like 996649622482-55; extract by shape so the
# (undocumented) separator for multi-attendee rows does not matter.
TR_ID_RE = re.compile(r"\b(\d{6,}-\d{2,3})\b")


def _cache_path(start: date, end: date):
    return RAW_MEETINGS_DIR / f"meetings_{start.isoformat()}_{end.isoformat()}.csv"


def _ttl(end: date) -> timedelta | None:
    """Recent windows stay hot: declarations are typically published with lag."""
    if (date.today() - end).days <= config.MEETINGS_HOT_WINDOW_DAYS:
        return timedelta(hours=config.MEETINGS_HOT_TTL_HOURS)
    return None


def _looks_like_export(body: str) -> bool:
    first_line = body.splitlines()[0] if body else ""
    return "member_id" in first_line


def _fetch_window(start: date, end: date) -> str:
    params = {
        "exportFormat": "CSV",
        "fromDate": start.strftime("%d/%m/%Y"),
        "toDate": end.strftime("%d/%m/%Y"),
    }
    return net.get(
        config.MEETINGS_EXPORT_URL,
        params=params,
        cache_path=_cache_path(start, end),
        ttl=_ttl(end),
        validate=_looks_like_export,
    )


def _parse_csv(body: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
    rows = list(reader)
    if rows:
        missing = EXPECTED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            log.warning("CSV export is missing expected columns: %s", sorted(missing))
    return rows


def _month_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Calendar-month windows clipped to [start, end]."""
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        yield cursor, min(end, next_month - timedelta(days=1))
        cursor = next_month


def _fetch_rows(start: date, end: date, truncated_days: list[str], window_count: list[int]) -> Iterator[dict]:
    """Yield raw CSV rows for [start, end], bisecting any window at the cap."""
    body = _fetch_window(start, end)
    rows = _parse_csv(body)
    window_count[0] += 1
    if len(rows) >= config.MEETINGS_EXPORT_ROW_CAP:
        if start == end:
            log.error(
                "single day %s hit the %d-row export cap; earliest rows of that "
                "day are unavailable from the source",
                start,
                config.MEETINGS_EXPORT_ROW_CAP,
            )
            truncated_days.append(start.isoformat())
            yield from rows
            return
        mid = start + (end - start) / 2
        log.info("window %s..%s hit the row cap; bisecting", start, end)
        yield from _fetch_rows(start, mid, truncated_days, window_count)
        yield from _fetch_rows(mid + timedelta(days=1), end, truncated_days, window_count)
        return
    yield from rows


def _normalize_row(row: dict) -> dict | None:
    mep_id = (row.get("member_id") or "").strip()
    mep_name = " ".join((row.get("member_name") or "").split())
    when = (row.get("meeting_date") or "").strip()
    if not when or not (mep_id or mep_name):
        return None
    refs = normalize.extract_procedure_refs(row.get("procedure_reference") or "")
    tr_ids = TR_ID_RE.findall(row.get("lobbyist_id") or "")
    capacity = " ".join((row.get("member_capacity") or "").split())
    return {
        "mep_id": mep_id or None,
        "mep_name": mep_name,
        "date": when,
        "capacity": capacity,
        "capacity_bucket": normalize.capacity_bucket(capacity),
        "title": " ".join((row.get("title") or "").split()),
        "procedure_code": refs[0][0] if refs else None,
        "procedure_keys": [key for _, key in refs],
        "attendees": normalize.clean_org(row.get("attendees") or ""),
        "registered": bool(tr_ids),
        "tr_ids": tr_ids,
    }


def fetch_meetings(since: date | None = None, until: date | None = None) -> tuple[list[dict], dict]:
    """Fetch, normalize, dedupe all term meetings. Returns (records, stats)."""
    since = since or config.TERM_START
    until = until or date.today()
    truncated_days: list[str] = []
    window_count = [0]
    raw_count = 0
    skipped = 0
    out_of_range = 0
    seen: set[tuple] = set()
    records: list[dict] = []

    for win_start, win_end in _month_windows(since, until):
        for row in _fetch_rows(win_start, win_end, truncated_days, window_count):
            raw_count += 1
            rec = _normalize_row(row)
            if rec is None:
                skipped += 1
                continue
            if not (since.isoformat() <= rec["date"] <= until.isoformat()):
                out_of_range += 1
                continue
            key = normalize.meeting_dedupe_key(rec)
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)

    records.sort(
        key=lambda r: (r["date"], r["mep_id"] or "", r["title"], r["attendees"], r["capacity"])
    )
    stats = {
        "raw_rows": raw_count,
        "deduplicated": raw_count - skipped - out_of_range - len(records),
        "skipped_malformed": skipped,
        "out_of_range": out_of_range,
        "kept": len(records),
        "windows_fetched": window_count[0],
        "truncated_days": truncated_days,
        "date_min": records[0]["date"] if records else None,
        "date_max": records[-1]["date"] if records else None,
        "by_capacity": dict(Counter(r["capacity_bucket"] for r in records)),
        "with_procedure": sum(1 for r in records if r["procedure_keys"]),
        "registered": sum(1 for r in records if r["registered"]),
    }
    return records, stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    records, stats = fetch_meetings()
    print("\n=== fetch_meetings summary ===")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print(f"  sample: {records[0] if records else None}")


if __name__ == "__main__":
    main()
