"""Shadow-rapporteur assignments for the current term, from the EP Open Data API.

Strategy (verified live): the UNFILTERED /procedures registry enumerates
process ids (paginated; ends with HTTP 204). The `year=` query filter is NOT
used because it silently drops records - measured June 2026: year=2026
returned 114 of 298 actual procedures, and live files such as 2023/0448(COD)
were missing entirely. /procedures/{id} exposes `had_participation`, where
personal participations carry `participation_role`
(def/ep-roles/RAPPORTEUR_SHADOW or RAPPORTEUR_SHADOW_OPINION), the person id,
the political group, an appointment date - and crucially `parliamentary_term`
("org/ep-10"), which scopes the assignment to the current term even on files
carried over from earlier years.

Procedure keys referenced by declared meetings (any year) are merged into the
scan set, so older carry-overs that MEPs demonstrably work on are never missed.

This module is deliberately decoupled: ``pipeline.run`` treats any hard
failure here as "View B unavailable" and the site degrades to View A only.
(OEIL procedure pages and Parltrack dumps remain documented fallbacks in the
README, but the official API covers the full denominator.)
"""
from __future__ import annotations

import logging
from datetime import timedelta

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


def _registry_ids() -> list[str]:
    """Every process id in the full /procedures registry (no year filter).

    The registry is ordered by process id and currently ~23k records; pages
    are cached briefly so a weekly refresh re-reads them. Pagination stops
    only on an EMPTY page (HTTP 204): the API has been observed returning
    slightly short pages mid-listing, so `len < limit` is NOT a reliable
    end-of-data signal.
    """
    ttl = timedelta(days=config.PROCEDURE_LIST_CURRENT_YEAR_TTL_DAYS)
    ids: list[str] = []
    offset = 0
    max_pages = 50  # backstop: 100k records is far beyond any plausible registry size
    for _ in range(max_pages):
        page = net.get_api_json(
            "/procedures",
            params={"limit": LIST_LIMIT, "offset": offset},
            cache_path=RAW_PROC_DIR / f"registry_offset{offset:06d}.json",
            ttl=ttl,
        )
        data = page.get("data") or []
        if not data:
            return sorted(set(ids))
        ids.extend(p["process_id"] for p in data if p.get("process_id"))
        offset += LIST_LIMIT
    log.warning("registry pagination hit the %d-page backstop", max_pages)
    return sorted(set(ids))


def _in_scope(process_id: str, years: list[int]) -> bool:
    year, _, serial = (process_id or "").partition("-")
    return year.isdigit() and serial != "" and int(year) in years


def _procedure_detail(process_id: str) -> dict | None:
    doc = net.get_api_json(
        f"/procedures/{process_id}",
        cache_path=RAW_PROC_DIR / f"proc_{process_id}.json",
        ttl=timedelta(days=config.PROCEDURE_DETAIL_TTL_DAYS),
    )
    data = doc.get("data") or []
    return data[0] if data else None


def _tail(ref) -> str | None:
    """Last path segment of an API reference.

    JSON-LD multi-valued fields may hand us a list (e.g. a reclassified
    procedure with two process_type values) - use the first string.
    """
    if isinstance(ref, list):
        ref = next((x for x in ref if isinstance(x, str)), None)
    if not isinstance(ref, str) or not ref:
        return None
    return ref.rsplit("/", 1)[-1]


def _as_list(value) -> list:
    """JSON-LD compacts single-element arrays to a bare value; undo that."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _extract_assignments(proc: dict) -> list[dict]:
    # Participations may arrive as a list of objects, a single object, or
    # (defensively) bare id strings - keep only the expanded objects.
    participations = [p for p in _as_list(proc.get("had_participation")) if isinstance(p, dict)]
    lead_committees = sorted(
        {
            _tail(org)
            for p in participations
            if p.get("participation_role") == LEAD_ROLE
            for org in _as_list(p.get("had_participant_organization"))
            if isinstance(org, str)
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
        if p.get("parliamentary_term") != config.TERM_ORG_ID:
            continue
        # A participation can carry several roles at once (e.g. shadow for the
        # report AND for an opinion) - emit one assignment row per mapped role.
        roles = [ROLE_MAP[r] for r in _as_list(p.get("participation_role")) if r in ROLE_MAP]
        if not roles:
            continue
        persons = [x for x in _as_list(p.get("had_participant_person")) if isinstance(x, str)]
        mep_id = _tail(persons[0]) if persons else None
        if not mep_id:
            continue
        committee = _tail(p.get("participation_in_name_of"))
        if not committee:
            committee = "+".join(c for c in lead_committees if c) or None
        for role in roles:
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


def fetch_assignments(
    years: list[int] | None = None,
    extra_keys: set[str] | None = None,
) -> tuple[list[dict], dict]:
    """Returns (assignment_records, stats). Raises net.FetchError on hard failure.

    `extra_keys` are procedure keys (YYYY-NNNN) observed elsewhere (declared
    meetings) that must be scanned regardless of their registration year.
    """
    years = years or config.ASSIGNMENT_SCAN_YEARS
    registry = _registry_ids()
    log.info("procedure registry: %d ids total", len(registry))
    in_scope = {pid for pid in registry if _in_scope(pid, years)}
    extras = {
        key for key in (extra_keys or set()) if normalize.PROC_KEY_RE.match(key)
    } - in_scope
    process_ids = sorted(in_scope | extras)
    log.info(
        "scanning %d procedures (%d registered %s-%s, %d extra via meeting references)",
        len(process_ids), len(in_scope), years[0], years[-1], len(extras),
    )
    assignments: list[dict] = []
    procedures_with_shadows = 0
    failed_ids: list[str] = []
    not_found_ids: list[str] = []
    for index, process_id in enumerate(process_ids, 1):
        try:
            proc = _procedure_detail(process_id)
        except net.NotFound:
            # Meeting-referenced ids can be typos in the source declarations.
            not_found_ids.append(process_id)
            continue
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
        "registry_total": len(registry),
        "extra_keys_scanned": len(extras),
        "procedures_scanned": len(process_ids),
        "procedures_with_term10_shadows": procedures_with_shadows,
        "assignments": len(deduped),
        "shadow_meps": len({r["mep_id"] for r in deduped}),
        "failed_procedure_ids": failed_ids,
        "not_found_ids": not_found_ids,
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
