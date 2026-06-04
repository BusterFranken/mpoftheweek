from datetime import date

from pipeline import build_metrics, config


def _mep(mep_id, name, shadow=False, group="PPE"):
    return {
        "id": mep_id,
        "name": name,
        "sort_name": name.upper(),
        "group": group,
        "group_label": group,
        "country": "France",
        "country_code": "FR",
        "committees": ["AGRI"],
        "is_shadow_rapporteur": shadow,
    }


def _meeting(mep_id, when="2025-01-10", bucket="member", keys=(), registered=False, org="ACME"):
    return {
        "mep_id": mep_id,
        "mep_name": "x",
        "date": when,
        "capacity": bucket,
        "capacity_bucket": bucket,
        "title": "t",
        "procedure_code": None,
        "procedure_keys": list(keys),
        "attendees": org,
        "registered": registered,
        "tr_ids": [],
    }


def _assignment(mep_id, key, role="shadow_rapporteur"):
    return {
        "procedure_key": key,
        "procedure_code": key.replace("-", "/") + "(COD)",
        "procedure_title": "T",
        "procedure_type": "COD",
        "committee": "AGRI",
        "mep_id": mep_id,
        "role": role,
        "group_at_appointment": "PPE",
        "appointed": "2024-09-01",
    }


MEPS = [
    _mep("1", "Alpha One", shadow=True),
    _mep("2", "Beta Two", shadow=True),
    _mep("3", "Gamma Three"),  # zero meetings - must still appear in View A
]

MEETINGS = [
    _meeting("1", bucket="shadow_rapporteur", keys=["2023-0001"], registered=True),
    _meeting("1", bucket="member", keys=["2023-0002"], org="Org B"),
    _meeting("1", bucket="member", org="acme"),  # same org, different case -> 2 distinct orgs total
    _meeting("2", bucket="member", registered=True, org="Solo"),
    _meeting("99", bucket="member"),  # departed MEP: ignored everywhere
]

ASSIGNMENTS = [
    _assignment("1", "2023-0001"),
    _assignment("1", "2023-0002", role="shadow_rapporteur_opinion"),
    _assignment("1", "2023-0003"),
    _assignment("2", "2023-0004"),
    _assignment("99", "2023-0005"),  # departed MEP: dropped from View B
]


def _build(history=None, overrides=None, today=date(2026, 6, 4)):
    return build_metrics.build(
        [dict(m) for m in MEPS],
        MEETINGS,
        ASSIGNMENTS,
        True,
        overrides or {},
        history or [],
        today,
    )


def test_view_a_rows_and_order():
    rankings, _ = _build()
    rows = rankings["view_a"]["rows"]
    assert [r["mep_id"] for r in rows] == ["1", "2", "3"]
    one = rows[0]
    assert one["meetings_total"] == 3
    assert one["meetings_by_capacity"]["shadow_rapporteur"] == 1
    assert one["meetings_by_capacity"]["member"] == 2
    assert one["distinct_organisations"] == 2  # ACME == acme
    assert one["share_registered"] == round(1 / 3, 4)
    assert one["rank"] == 1
    zero = rows[2]
    assert zero["meetings_total"] == 0
    assert zero["share_registered"] is None
    assert zero["first_meeting_date"] is None


def test_view_b_rows_match_any_capacity():
    rankings, _ = _build()
    rows = rankings["view_b"]["rows"]
    assert [r["mep_id"] for r in rows] == ["1", "2"]  # MEP 99 dropped
    one = rows[0]
    # 0001 matched via shadow meeting, 0002 matched via *member* meeting, 0003 unmatched
    assert one["files_shadowed"] == 3
    assert one["files_with_related_meeting"] == 2
    assert one["coverage_pct"] == 66.7
    assert one["shadow_meetings_total"] == 1
    two = rows[1]
    assert two["files_shadowed"] == 1
    assert two["files_with_related_meeting"] == 0
    assert two["coverage_pct"] == 0.0


def test_build_is_deterministic():
    r1, h1 = _build()
    r2, h2 = _build()
    assert r1 == r2
    assert h1 == h2


def test_feature_pick_rotation_and_week_stability():
    today = date(2026, 6, 4)  # ISO week 2026-W23
    rankings, history = _build(today=today)
    assert rankings["weekly"]["feature_a"]["mep_id"] == "1"
    assert {(h["week"], h["view"]) for h in history} == {("2026-W23", "a"), ("2026-W23", "b")}

    # featured last week -> rotated out this week
    prior = [{"week": "2026-W22", "view": "a", "mep_id": "1"}]
    rankings2, _ = _build(history=prior, today=today)
    assert rankings2["weekly"]["feature_a"]["mep_id"] == "2"

    # featured long ago (> rotation window) -> eligible again
    weeks_ago = config.FEATURE_ROTATION_WEEKS + 1
    old = [{"week": f"2026-W{23 - weeks_ago:02d}", "view": "a", "mep_id": "1"}]
    rankings3, _ = _build(history=old, today=today)
    assert rankings3["weekly"]["feature_a"]["mep_id"] == "1"

    # an entry already recorded for the current week is reused as-is
    fixed = [{"week": "2026-W23", "view": "a", "mep_id": "2"}]
    rankings4, history4 = _build(history=fixed, today=today)
    assert rankings4["weekly"]["feature_a"] == {"mep_id": "2", "source": "history", "week": "2026-W23"}
    assert len([h for h in history4 if h["view"] == "a"]) == 1  # idempotent


def test_override_beats_everything():
    rankings, _ = _build(overrides={"featured_mep_id": "3"})
    assert rankings["weekly"]["feature_a"]["mep_id"] == "3"
    assert rankings["weekly"]["feature_a"]["source"] == "override"


def test_watchlist_threshold_order_and_exclusions():
    # Give MEP 2 enough files to qualify: 1 matched of 4 (25%); MEP 1: 2 of 3 (66.7%)
    assignments = ASSIGNMENTS + [
        _assignment("2", "2023-0010"),
        _assignment("2", "2023-0011"),
        _assignment("2", "2023-0012"),
    ]
    meetings = MEETINGS + [_meeting("2", keys=["2023-0010"])]
    rankings, _ = build_metrics.build(
        [dict(m) for m in MEPS], meetings, assignments, True, {}, [], date(2026, 6, 4)
    )
    assert rankings["weekly"]["watchlist"] == ["2", "1"]  # worst coverage first

    rankings_x, _ = build_metrics.build(
        [dict(m) for m in MEPS], meetings, assignments, True,
        {"watchlist_exclude_ids": ["2"]}, [], date(2026, 6, 4),
    )
    assert rankings_x["weekly"]["watchlist"] == ["1"]


def test_view_b_unavailable_degrades():
    rankings, _ = build_metrics.build(
        [dict(m) for m in MEPS], MEETINGS, [], False, {}, [], date(2026, 6, 4)
    )
    assert rankings["view_b"]["available"] is False
    assert rankings["view_b"]["rows"] == []
    assert rankings["weekly"]["feature_b"] is None
    assert rankings["weekly"]["watchlist"] == []
    # View A still fully present
    assert len(rankings["view_a"]["rows"]) == 3
