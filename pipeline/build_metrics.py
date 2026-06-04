"""Compute View A / View B metrics, both leaderboards and the weekly picks.

All ordering is fully deterministic (explicit tie-breaks ending in the MEP id)
so that identical inputs always produce byte-identical rankings.json.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import config, normalize

log = logging.getLogger(__name__)

CAPACITY_KEYS = ("shadow_rapporteur", "rapporteur", "committee_chair", "member", "other")

WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


# --- View A: disclosure volume ----------------------------------------------------


def view_a_rows(meps: list[dict], meetings_by_mep: dict[str, list[dict]]) -> list[dict]:
    rows = []
    for mep in meps:
        ms = meetings_by_mep.get(mep["id"], [])
        caps = Counter(m["capacity_bucket"] for m in ms)
        orgs = {normalize.norm_name(m["attendees"]) for m in ms if m["attendees"]}
        registered = sum(1 for m in ms if m["registered"])
        dates = sorted(m["date"] for m in ms)
        rows.append(
            {
                "mep_id": mep["id"],
                "meetings_total": len(ms),
                "meetings_by_capacity": {k: caps.get(k, 0) for k in CAPACITY_KEYS},
                "distinct_organisations": len(orgs),
                "share_registered": round(registered / len(ms), 4) if ms else None,
                "first_meeting_date": dates[0] if dates else None,
                "last_meeting_date": dates[-1] if dates else None,
            }
        )
    return rows


# --- View B: shadow-rapporteur compliance --------------------------------------------


def view_b_rows(
    meps_by_id: dict[str, dict],
    meetings_by_mep: dict[str, list[dict]],
    assignments: list[dict],
) -> list[dict]:
    files_by_mep: dict[str, set[str]] = defaultdict(set)
    for a in assignments:
        files_by_mep[a["mep_id"]].add(a["procedure_key"])

    rows = []
    for mep_id, files in files_by_mep.items():
        if mep_id not in meps_by_id:
            continue  # shadow appointments of MEPs who since left the Parliament
        ms = meetings_by_mep.get(mep_id, [])
        met_keys = {key for m in ms for key in m["procedure_keys"]}
        matched = files & met_keys
        rows.append(
            {
                "mep_id": mep_id,
                "files_shadowed": len(files),
                "files_with_related_meeting": len(matched),
                "coverage_pct": round(100 * len(matched) / len(files), 1),
                "shadow_meetings_total": sum(
                    1 for m in ms if m["capacity_bucket"] == "shadow_rapporteur"
                ),
            }
        )
    return rows


# --- Ordering --------------------------------------------------------------------------


def _name_key(meps_by_id: dict[str, dict], mep_id: str) -> str:
    mep = meps_by_id.get(mep_id) or {}
    return mep.get("sort_name") or mep.get("name") or ""


def sort_view_a(rows: list[dict], meps_by_id: dict[str, dict]) -> list[dict]:
    """Best->worst: most published meetings first."""
    return sorted(
        rows,
        key=lambda r: (
            -r["meetings_total"],
            -r["distinct_organisations"],
            _name_key(meps_by_id, r["mep_id"]),
            r["mep_id"],
        ),
    )


def sort_view_b(rows: list[dict], meps_by_id: dict[str, dict]) -> list[dict]:
    """Best->worst: coverage, then matched files, then shadow meetings."""
    return sorted(
        rows,
        key=lambda r: (
            -r["files_with_related_meeting"] / r["files_shadowed"] if r["files_shadowed"] else 0.0,
            -r["files_with_related_meeting"],
            -r["shadow_meetings_total"],
            _name_key(meps_by_id, r["mep_id"]),
            r["mep_id"],
        ),
    )


def _ranked(rows: list[dict]) -> list[dict]:
    return [{**row, "rank": index} for index, row in enumerate(rows, 1)]


# --- Weekly picks --------------------------------------------------------------------------


def iso_week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _week_start(label: str) -> date:
    m = WEEK_RE.match(label)
    if not m:
        return date.min
    return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)


def pick_feature(
    ordered_rows: list[dict],
    view: str,
    history: list[dict],
    today: date,
    overrides: dict,
    eligible: set[str] | None,
) -> dict | None:
    """Deterministic 'MEP of the Week' for one view.

    Top of the board among `eligible` ids (None = all rows), skipping anyone
    featured in the previous FEATURE_ROTATION_WEEKS weeks. A pick already
    recorded for the current week stays stable across re-runs; an override in
    overrides.json beats everything.
    """
    week = iso_week_label(today)
    override = overrides.get("featured_mep_id")
    if override:
        return {"mep_id": str(override), "source": "override", "week": week}

    for entry in history:
        if entry.get("week") == week and entry.get("view") == view:
            return {"mep_id": entry["mep_id"], "source": "history", "week": week}

    this_week_start = _week_start(week)
    cutoff = this_week_start - timedelta(weeks=config.FEATURE_ROTATION_WEEKS)
    recently_featured = {
        e["mep_id"]
        for e in history
        if e.get("view") == view and cutoff < _week_start(e.get("week", "")) < this_week_start
    }
    candidates = [
        r["mep_id"]
        for r in ordered_rows
        if eligible is None or r["mep_id"] in eligible
    ]
    if not candidates:
        return None
    chosen = next((c for c in candidates if c not in recently_featured), candidates[0])
    return {"mep_id": chosen, "source": "auto", "week": week}


def pick_watchlist(ordered_b_rows: list[dict], meps_by_id: dict[str, dict], overrides: dict) -> list[str]:
    """Bottom of View B, restricted to meaningful denominators (>= N files)."""
    excluded = {str(x) for x in overrides.get("watchlist_exclude_ids") or []}
    eligible = [
        r
        for r in ordered_b_rows
        if r["files_shadowed"] >= config.WATCHLIST_MIN_FILES_SHADOWED
        and r["mep_id"] not in excluded
    ]
    worst_first = sorted(
        eligible,
        key=lambda r: (
            r["files_with_related_meeting"] / r["files_shadowed"] if r["files_shadowed"] else 0.0,
            -r["files_shadowed"],
            r["shadow_meetings_total"],
            _name_key(meps_by_id, r["mep_id"]),
            r["mep_id"],
        ),
    )
    return [r["mep_id"] for r in worst_first[: config.WATCHLIST_SIZE]]


# --- Entry point -----------------------------------------------------------------------------


def build(
    meps: list[dict],
    meetings: list[dict],
    assignments: list[dict],
    view_b_available: bool,
    overrides: dict,
    history: list[dict],
    today: date,
) -> tuple[dict, list[dict]]:
    """Returns (rankings_document, updated_history)."""
    meps_by_id = {m["id"]: m for m in meps}
    meetings_by_mep: dict[str, list[dict]] = defaultdict(list)
    for m in meetings:
        if m["mep_id"] in meps_by_id:
            meetings_by_mep[m["mep_id"]].append(m)

    a_rows = _ranked(sort_view_a(view_a_rows(meps, meetings_by_mep), meps_by_id))
    b_rows = (
        _ranked(sort_view_b(view_b_rows(meps_by_id, meetings_by_mep, assignments), meps_by_id))
        if view_b_available
        else []
    )

    shadow_ids = {m["id"] for m in meps if m.get("is_shadow_rapporteur")}
    feature_a = pick_feature(a_rows, "a", history, today, overrides, shadow_ids or None)
    feature_b = pick_feature(b_rows, "b", history, today, overrides, None) if b_rows else None
    watchlist = pick_watchlist(b_rows, meps_by_id, overrides) if b_rows else []

    week = iso_week_label(today)
    updated_history = list(history)
    for view, feature in (("a", feature_a), ("b", feature_b)):
        if feature and not any(
            e.get("week") == week and e.get("view") == view for e in updated_history
        ):
            updated_history.append({"week": week, "view": view, "mep_id": feature["mep_id"]})
    updated_history.sort(key=lambda e: (e.get("week", ""), e.get("view", ""), e.get("mep_id", "")))

    rankings = {
        "week": week,
        "view_a": {"rows": a_rows},
        "view_b": {"available": view_b_available, "rows": b_rows},
        "weekly": {
            "feature_a": feature_a,
            "feature_b": feature_b,
            "watchlist": watchlist,
        },
    }
    return rankings, updated_history
