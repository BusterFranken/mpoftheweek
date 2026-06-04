from datetime import date

import pytest

from pipeline import config, fetch_meetings


HEADER = "title,member_id,member_name,meeting_date,member_capacity,procedure_reference,attendees,lobbyist_id"


def _csv(rows: list[str]) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def test_parse_csv_handles_bom_and_quoted_commas():
    body = "﻿" + _csv(
        ['"Priorities, new legislature",257727,MARAN Pierfrancesco,2024-09-06,Member,,"Org, with, commas",996649622482-55']
    )
    rows = fetch_meetings._parse_csv(body)
    assert len(rows) == 1
    assert rows[0]["title"] == "Priorities, new legislature"
    assert rows[0]["attendees"] == "Org, with, commas"
    assert rows[0]["member_id"] == "257727"


def test_normalize_row_full():
    raw = {
        "title": "  CLP   Regulation ",
        "member_id": " 107212 ",
        "member_name": "NOVAKOV  Andrey",
        "meeting_date": "2024-09-06",
        "member_capacity": "Shadow rapporteur",
        "procedure_reference": "2022/0432(COD)",
        "attendees": " International  Fragrance Association ",
        "lobbyist_id": "14130436110-87",
    }
    rec = fetch_meetings._normalize_row(raw)
    assert rec == {
        "mep_id": "107212",
        "mep_name": "NOVAKOV Andrey",
        "date": "2024-09-06",
        "capacity": "Shadow rapporteur",
        "capacity_bucket": "shadow_rapporteur",
        "title": "CLP Regulation",
        "procedure_code": "2022/0432(COD)",
        "procedure_keys": ["2022-0432"],
        "attendees": "International Fragrance Association",
        "registered": True,
        "tr_ids": ["14130436110-87"],
    }


def test_normalize_row_multiple_tr_ids_any_separator():
    raw = {
        "title": "t",
        "member_id": "1",
        "member_name": "X Y",
        "meeting_date": "2025-01-01",
        "member_capacity": "Member",
        "procedure_reference": "",
        "attendees": "A and B",
        "lobbyist_id": "996649622482-55; 14130436110-87",
    }
    rec = fetch_meetings._normalize_row(raw)
    assert rec["tr_ids"] == ["996649622482-55", "14130436110-87"]
    assert rec["registered"] is True


def test_normalize_row_rejects_empty():
    assert fetch_meetings._normalize_row({"meeting_date": "", "member_id": "1"}) is None
    assert fetch_meetings._normalize_row({"meeting_date": "2025-01-01", "member_id": "", "member_name": ""}) is None


def test_month_windows_clip():
    windows = list(fetch_meetings._month_windows(date(2024, 7, 16), date(2024, 9, 10)))
    assert windows == [
        (date(2024, 7, 16), date(2024, 7, 31)),
        (date(2024, 8, 1), date(2024, 8, 31)),
        (date(2024, 9, 1), date(2024, 9, 10)),
    ]


def test_fetch_rows_bisects_on_cap(monkeypatch):
    """A window at the cap is split; sub-windows under the cap are used as-is."""
    cap = config.MEETINGS_EXPORT_ROW_CAP

    def fake_window(start: date, end: date) -> str:
        days = (end - start).days + 1
        if days > 1:
            # parent window: pretend it overflowed (cap rows returned)
            return _csv([f"t,1,A B,{start.isoformat()},Member,,O," for _ in range(cap)])
        return _csv([f"t,1,A B,{start.isoformat()},Member,,O,"])

    monkeypatch.setattr(fetch_meetings, "_fetch_window", fake_window)
    truncated: list[str] = []
    count = [0]
    rows = list(fetch_meetings._fetch_rows(date(2025, 1, 1), date(2025, 1, 4), truncated, count))
    # 4 single-day windows, one row each
    assert len(rows) == 4
    assert {r["meeting_date"] for r in rows} == {"2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"}
    assert truncated == []
    assert count[0] >= 5  # parent + intermediate + leaves


def test_fetch_rows_flags_truncated_single_day(monkeypatch):
    cap = config.MEETINGS_EXPORT_ROW_CAP

    def fake_window(start: date, end: date) -> str:
        return _csv([f"t,1,A B,{start.isoformat()},Member,,O," for _ in range(cap)])

    monkeypatch.setattr(fetch_meetings, "_fetch_window", fake_window)
    truncated: list[str] = []
    rows = list(fetch_meetings._fetch_rows(date(2025, 1, 1), date(2025, 1, 1), truncated, [0]))
    assert len(rows) == cap
    assert truncated == ["2025-01-01"]


def test_fetch_meetings_dedupes_and_filters(monkeypatch):
    body = _csv(
        [
            "Banking,1,A B,2024-07-16,Member,,ACME,",
            "Banking,1,A B,2024-07-16,Member,,ACME,",        # exact duplicate
            "Old,1,A B,2024-07-10,Member,,ACME,",            # before term start
        ]
    )
    monkeypatch.setattr(fetch_meetings, "_fetch_window", lambda s, e: body)
    records, stats = fetch_meetings.fetch_meetings(date(2024, 7, 16), date(2024, 7, 31))
    assert stats["kept"] == 1
    assert stats["deduplicated"] >= 1
    assert stats["out_of_range"] >= 1
    assert records[0]["date"] == "2024-07-16"
