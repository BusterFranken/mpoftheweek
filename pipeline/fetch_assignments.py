"""Shadow-rapporteur assignments for the current term, from the EP Open Data API.

Strategy (verified live): /procedures?year=YYYY lists process ids;
/procedures/{id} exposes `had_participation`, where personal participations
carry `participation_role` (def/ep-roles/RAPPORTEUR_SHADOW or
RAPPORTEUR_SHADOW_OPINION), the person id, the political group, an appointment
date - and crucially `parliamentary_term` ("org/ep-10"), which scopes the
assignment to the current term even on files carried over from earlier years.

This module is deliberately decoupled: ``pipeline.run`` treats any hard
failure here as "View B unavailable" and the site degrades to View A only.
(OEIL procedure pages and Parltrack dumps remain documented fallbacks in the
README, but the official API covers the full denominator.)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import config, net, normalize

log = logging.getLogger(__name__)

RAW_PROC_DIR = config.RAW_DIR / "procedures"
LIST_LIMIT = 2000

ROLE_MAP = {
    "def/ep-roles/RAPPORTEUR_SHADOW": "shadow_rapporteur",
    "def/ep-roles/RAPPORTEUR_SHADOW_OPINION": "shadow_rapporteur_opinion",
}
LEAD_ROLE = "def/ep-roles/COMMITTEE_LEAD"

# Tolerate a few unreachable procedure documents before declaring the whole
# source unusable (the API occasionally 500s on single ids).
MAX_FAILURE_SHARE = 0.05


def _year_list(year: int) -> list[dict]:
    """All procedures registered in `year` (paginated defensively)."""
    ttl = timedelta(days=config.PROCEDURE_LIST_CURRENT_YEAR_TTL_DAYS) if year == date.today().year else None
    procedures: list[dict] = []
    offset = 0
    while True:
        suffix = f"_offset{offset}" if offset else ""
        page = net.get_api_json(
            "/procedures",
            params={"year": year, "limit": LIST_LIMIT, "offset": offset},
            cache_path=RAW_PROC_DIR / f"list_{year}{suffix}.json",
            ttl=ttl,
        )
        data = page.get("data") or []
        procedures.extend(data)
        if len(data) < LIST_LIMIT:
            return procedures
        offset += LIST_LIMIT


def _procedure_detail(process_id: str) -> dict | None:
    doc = net.get_api_json(
        f"/procedures/{process_id}",
        cache_path=RAW_PROC_DIR / f"proc_{process_id}.json",
        ttl=timedelta(days=config.PROCEDURE_DETAIL_TTL_DAYS),
    )
    data = doc.get("data") or []
    return data[0] if data else None


def _tail(ref: str | None) -> str | None:
    return ref.rsplit("/", 1)[-1] if ref else None


def _extract_assignments(proc: dict) -> list[dict]:
    participations = proc.get("had_participation") or []
    lead_committees = sorted(
        {
            _tail(org)
            for p in participations
            if p.get("participation_role") == LEAD_ROLE
            for org in (p.get("had_participant_organization") or [])
            if org
        }
    )
    process_id = proc.get("process_id") or _tail(proc.get("id")) or ""
    proc_type = _tail(proc.get("process_type"))
    label = proc.get("label")
    display = (
        label
        if isinstance(label, str) and normalize.proc_display_to_key(label)
        else normalize.proc_key_to_display(process_id, proc_type)
    )
    titles = proc.get("process_title")
    title = titles.get("en") if isinstance(titles, dict) else None

    rows: list[dict] = []
    for p in participations:
        role = ROLE_MAP.get(p.get("participation_role") or "")
        if role is None:
            continue
        if p.get("parliamentary_term") != config.TERM_ORG_ID:
            continue
        persons = p.get("had_participant_person") or []
        mep_id = _tail(persons[0]) if persons else None
        if not mep_id:
            continue
        committee = _tail(p.get("participation_in_name_of"))
        if not committee:
            committee = "+".join(c for c in lead_committees if c) or None
        rows.append(
            {
                "procedure_key": process_id,
                "procedure_code": display,
                "procedure_title": title,
                "procedure_type": proc_type,
                "committee": committee,
                "mep_id": mep_id,
                "role": role,
                "group_at_appointment": _tail(p.get("politicalGroup")),
                "appointed": p.get("activity_date"),
            }
        )
    return rows


def dedupe_assignments(rows: list[dict]) -> list[dict]:
    """One row per (file, MEP, role, committee); keep the earliest appointment."""
    best: dict[tuple, dict] = {}
    for row in rows:
        key = (row["procedure_key"], row["mep_id"], row["role"], row["committee"])
        kept = best.get(key)
        if kept is None or (row["appointed"] or "9999") < (kept["appointed"] or "9999"):
            best[key] = row
    return sorted(
        best.values(),
        key=lambda r: (r["procedure_key"], r["mep_id"], r["role"], r["committee"] or ""),
    )


def fetch_assignments(years: list[int] | None = None) -> tuple[list[dict], dict]:
    """Returns (assignment_records, stats). Raises net.FetchError on hard failure."""
    years = years or config.ASSIGNMENT_SCAN_YEARS
    process_ids: list[str] = []
    for year in years:
        listed = _year_list(year)
        process_ids.extend(p.get("process_id") for p in listed if p.get("process_id"))
        log.info("year %d: %d procedures listed", year, len(listed))

    process_ids = sorted(set(process_ids))
    assignments: list[dict] = []
    procedures_with_shadows = 0
    failed_ids: list[str] = []
    for index, process_id in enumerate(process_ids, 1):
        try:
            proc = _procedure_detail(process_id)
        except net.FetchError as exc:
            log.warning("procedure %s unavailable: %s", process_id, exc)
            failed_ids.append(process_id)
            continue
        if proc is None:
            continue
        rows = _extract_assignments(proc)
        if rows:
            procedures_with_shadows += 1
            assignments.extend(rows)
        if index % 250 == 0:
            log.info("procedure details processed: %d/%d", index, len(process_ids))

    if process_ids and len(failed_ids) > len(process_ids) * MAX_FAILURE_SHARE:
        raise net.FetchError(
            f"{len(failed_ids)}/{len(process_ids)} procedure documents failed - "
            "assignment data too incomplete to publish"
        )

    deduped = dedupe_assignments(assignments)

    stats = {
        "years_scanned": years,
        "procedures_scanned": len(process_ids),
        "procedures_with_term10_shadows": procedures_with_shadows,
        "assignments": len(deduped),
        "shadow_meps": len({r["mep_id"] for r in deduped}),
        "failed_procedure_ids": failed_ids,
    }
    return deduped, stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    records, stats = fetch_assignments()
    print("\n=== fetch_assignments summary ===")
    for key, value in stats.items():
        print(f"  {key}: {value if not isinstance(value, list) or len(value) < 8 else f'{len(value)} items'}")
    print(f"  sample: {records[0] if records else None}")


if __name__ == "__main__":
    main()
