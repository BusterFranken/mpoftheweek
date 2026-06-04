"""Orchestrate the full data refresh.

Usage::

    python -m pipeline.run [--no-cache] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                           [--skip-assignments]

Writes deterministic JSON build inputs to site/src/data/ and prints a run
summary (counts, coverage, unmatched names, warnings).
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import date, datetime, timezone

from . import (
    build_metrics,
    config,
    fetch_assignments,
    fetch_meetings,
    fetch_meps,
    io_utils,
    net,
)

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m pipeline.run", description=__doc__)
    parser.add_argument("--no-cache", action="store_true", help="ignore cached raw downloads")
    parser.add_argument("--since", type=date.fromisoformat, default=None, help="start of meeting range (default: term start)")
    parser.add_argument("--until", type=date.fromisoformat, default=None, help="end of meeting range (default: today)")
    parser.add_argument("--skip-assignments", action="store_true", help="skip Source 3; site degrades to View A only")
    return parser.parse_args(argv)


def _aggregate_unmatched(meetings: list[dict], current_ids: set[str], outgoing_ids: set[str]) -> list[dict]:
    """Meetings declared by people who are not current MEPs (e.g. left mid-term)."""
    counts: Counter = Counter()
    names: dict[str, str] = {}
    for m in meetings:
        mep_id = m["mep_id"]
        if mep_id and mep_id in current_ids:
            continue
        key = mep_id or f"name:{m['mep_name']}"
        counts[key] += 1
        names[key] = m["mep_name"]
    out = []
    for key, count in counts.most_common():
        mep_id = None if key.startswith("name:") else key
        status = "left_term" if mep_id and mep_id in outgoing_ids else "unknown"
        out.append({"mep_id": mep_id, "name": names[key], "meetings": count, "status": status})
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    net.NO_CACHE = args.no_cache
    since = args.since or config.TERM_START
    until = args.until or date.today()
    today = date.today()

    print(f"MEP of the Week refresh - {config.TERM_LABEL}, meetings {since} .. {until}")

    # --- Source 2: MEP register ------------------------------------------------
    meps, committees_map, outgoing_ids, mep_stats = fetch_meps.fetch_meps()

    # --- Source 1: declared meetings -------------------------------------------
    meetings, meeting_stats = fetch_meetings.fetch_meetings(since, until)

    # --- Source 3: shadow assignments (decoupled; site degrades without it) ----
    assignments: list[dict] = []
    assign_stats: dict = {}
    view_b_available = False
    view_b_reason = "ok"
    if args.skip_assignments:
        view_b_reason = "skipped via --skip-assignments"
        log.warning("assignments skipped; View B will be unavailable")
    else:
        try:
            assignments, assign_stats = fetch_assignments.fetch_assignments()
            view_b_available = True
        except Exception as exc:  # noqa: BLE001 - degrade, never take the site down
            view_b_reason = f"assignment source failed: {exc}"
            log.exception("assignments unavailable; View B will be degraded")

    # --- Joins -------------------------------------------------------------------
    current_ids = {m["id"] for m in meps}
    if view_b_available:
        shadow_ids = {a["mep_id"] for a in assignments}
    else:
        # Fallback: shadow roles observable from declared meeting capacities.
        shadow_ids = {
            m["mep_id"]
            for m in meetings
            if m["mep_id"] and m["capacity_bucket"] == "shadow_rapporteur"
        }
    for mep in meps:
        mep["is_shadow_rapporteur"] = mep["id"] in shadow_ids

    unmatched = _aggregate_unmatched(meetings, current_ids, outgoing_ids)

    # --- Metrics & picks ------------------------------------------------------------
    overrides = io_utils.read_json(config.OVERRIDES_PATH, {}) or {}
    history = io_utils.read_json(config.SITE_DATA_DIR / "weekly_history.json", []) or []
    rankings, new_history = build_metrics.build(
        meps, meetings, assignments, view_b_available, overrides, history, today
    )
    rankings["committees"] = dict(sorted(committees_map.items()))

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "term": config.TERM,
        "term_label": config.TERM_LABEL,
        "date_range": {"from": since.isoformat(), "to": until.isoformat()},
        "source_urls": {
            "meetings_export": config.MEETINGS_EXPORT_URL,
            "open_data_api": config.EP_API_BASE,
            "oeil": "https://oeil.europarl.europa.eu",
        },
        "licence": "Data © European Union / European Parliament, reused under CC BY 4.0",
        "counts": {
            "meps": len(meps),
            "meetings": len(meetings),
            "meetings_registered": meeting_stats["registered"],
            "meetings_with_procedure": meeting_stats["with_procedure"],
            "assignments": len(assignments),
            "shadow_rapporteurs": len(shadow_ids & current_ids),
            "procedures_scanned": assign_stats.get("procedures_scanned", 0),
            "unmatched_meeting_declarants": len(unmatched),
        },
        "view_b": {"available": view_b_available, "reason": view_b_reason},
        "rules": {
            "feature_rotation_weeks": config.FEATURE_ROTATION_WEEKS,
            "watchlist_min_files_shadowed": config.WATCHLIST_MIN_FILES_SHADOWED,
            "watchlist_size": config.WATCHLIST_SIZE,
        },
        "unmatched_names": unmatched,
        "warnings": {
            "truncated_days": meeting_stats["truncated_days"],
            "mep_detail_failures": mep_stats["detail_failures"],
            "failed_procedure_ids": assign_stats.get("failed_procedure_ids", []),
        },
    }

    # --- Write build inputs -----------------------------------------------------------
    data_dir = config.SITE_DATA_DIR
    io_utils.write_json(data_dir / "meps.json", meps, records=True)
    io_utils.write_json(data_dir / "meetings.json", meetings, records=True)
    io_utils.write_json(data_dir / "assignments.json", assignments, records=True)
    io_utils.write_json(data_dir / "rankings.json", rankings)
    io_utils.write_json(data_dir / "meta.json", meta)
    io_utils.write_json(data_dir / "weekly_history.json", new_history)

    # --- Run summary ---------------------------------------------------------------------
    by_id = {m["id"]: m for m in meps}
    a_rows = rankings["view_a"]["rows"]
    b_rows = rankings["view_b"]["rows"]
    print("\n================ RUN SUMMARY ================")
    print(f"generated_at        : {meta['generated_at']}")
    print(f"MEPs (current)      : {len(meps)}  ({mep_stats['outgoing_meps']} left during term)")
    print(f"meetings kept       : {len(meetings)}  ({meeting_stats['raw_rows']} raw rows, "
          f"{meeting_stats['deduplicated']} duplicates, {meeting_stats['windows_fetched']} windows)")
    print(f"date coverage       : {meeting_stats['date_min']} .. {meeting_stats['date_max']}")
    print(f"with procedure ref  : {meeting_stats['with_procedure']}")
    print(f"registered counterpart share: "
          f"{meeting_stats['registered'] / len(meetings):.1%}" if meetings else "n/a")
    print(f"assignments         : {len(assignments)} (View B available: {view_b_available}; {view_b_reason})")
    print(f"shadow rapporteurs  : {meta['counts']['shadow_rapporteurs']}")
    if a_rows:
        top = by_id[a_rows[0]["mep_id"]]["name"]
        print(f"View A top          : {top} ({a_rows[0]['meetings_total']} meetings)")
    if b_rows:
        top = by_id[b_rows[0]["mep_id"]]["name"]
        print(f"View B top          : {top} ({b_rows[0]['coverage_pct']}% of {b_rows[0]['files_shadowed']} files)")
    if rankings["weekly"]["feature_a"]:
        print(f"MEP of the Week (A) : {by_id.get(rankings['weekly']['feature_a']['mep_id'], {}).get('name')}")
    if rankings["weekly"]["feature_b"]:
        print(f"MEP of the Week (B) : {by_id.get(rankings['weekly']['feature_b']['mep_id'], {}).get('name')}")
    if rankings["weekly"]["watchlist"]:
        print(f"Watchlist           : {[by_id.get(i, {}).get('name') for i in rankings['weekly']['watchlist']]}")
    if unmatched:
        head = ", ".join(f"{u['name']} ({u['meetings']}, {u['status']})" for u in unmatched[:5])
        print(f"unmatched declarants: {len(unmatched)} - top: {head}")
    if meeting_stats["truncated_days"]:
        print(f"WARNING truncated days (1000-row cap): {meeting_stats['truncated_days']}")
    print("=============================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
