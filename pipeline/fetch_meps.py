"""Fetch current MEPs (id, name, group, country, committees) from the EP API.

Source: EP Open Data API v2 (CC-BY 4.0). The list endpoint already carries the
country of representation and the current political-group short label - both
read from the API, never hardcoded. Committee memberships come from the
per-MEP detail document; committee org-ids resolve to codes/names through
/corporate-bodies/{id}.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from . import config, net, normalize

log = logging.getLogger(__name__)

RAW_MEPS_DIR = config.RAW_DIR / "meps"
RAW_CB_DIR = config.RAW_DIR / "corporate_bodies"

PAGE_SIZE = 50
GROUP_CLASS = "EU_POLITICAL_GROUP"

# ISO codes -> display names for the 27 member states. The EP uses "EL" for
# Greece in some datasets and "GR" in others; accept both. Unknown codes fall
# back to the code itself, so a future enlargement degrades gracefully.
EU_COUNTRIES = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia",
    "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia",
    "FI": "Finland", "FR": "France", "DE": "Germany", "EL": "Greece",
    "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta",
    "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
}

_cb_cache: dict[str, dict] = {}


def _detail_ttl() -> timedelta:
    return timedelta(days=config.MEP_DETAIL_TTL_DAYS)


def _list_pages(endpoint: str, cache_prefix: str, extra_params: dict | None = None) -> list[dict]:
    people: list[dict] = []
    offset = 0
    while True:
        params = {"offset": offset, "limit": PAGE_SIZE}
        if extra_params:
            params.update(extra_params)
        page = net.get_api_json(
            endpoint,
            params=params,
            cache_path=RAW_MEPS_DIR / f"{cache_prefix}_offset{offset}.json",
            ttl=_detail_ttl(),
        )
        data = page.get("data") or []
        people.extend(data)
        if len(data) < PAGE_SIZE:
            return people
        offset += PAGE_SIZE


def _corporate_body(org_ref: str) -> dict:
    """Resolve an org reference ('org/6358' or 'org/AGRI') to code + name."""
    org_id = org_ref.rsplit("/", 1)[-1]
    if org_id in _cb_cache:
        return _cb_cache[org_id]
    doc = net.get_api_json(
        f"/corporate-bodies/{org_id}",
        cache_path=RAW_CB_DIR / f"{org_id}.json",
        ttl=None,  # codes and names of bodies are stable
    )
    body = (doc.get("data") or [{}])[0]
    pref = body.get("prefLabel")
    name = pref.get("en") if isinstance(pref, dict) else None
    info = {"code": body.get("label") or org_id, "name": name or body.get("label") or org_id}
    _cb_cache[org_id] = info
    return info


def _membership_active(membership: dict, today_iso: str) -> bool:
    period = membership.get("memberDuring") or {}
    start = period.get("startDate")
    end = period.get("endDate")
    if not start:
        return False
    return start <= today_iso and (end is None or end >= today_iso)


def _slug(given: str, family: str) -> str:
    s = normalize.strip_accents(f"{given} {family}").upper()
    return re.sub(r"[^A-Z0-9]+", "_", s).strip("_")


def _mep_record(person: dict, committees_map: dict[str, str], today_iso: str) -> dict:
    mep_id = str(person.get("identifier"))
    given = person.get("givenName") or ""
    family = person.get("familyName") or ""
    committees: list[str] = []
    group_org: str | None = None

    try:
        detail = net.get_api_json(
            f"/meps/{mep_id}",
            cache_path=RAW_MEPS_DIR / f"mep_{mep_id}.json",
            ttl=_detail_ttl(),
        )
        body = (detail.get("data") or [{}])[0]
        given = body.get("givenName") or given
        family = body.get("familyName") or family
        for membership in body.get("hasMembership") or []:
            if not _membership_active(membership, today_iso):
                continue
            org = membership.get("organization")
            if not org:
                continue
            cls = (membership.get("membershipClassification") or "").rsplit("/", 1)[-1]
            if cls.startswith("COMMITTEE"):
                cb = _corporate_body(org)
                committees_map[cb["code"]] = cb["name"]
                if cb["code"] not in committees:
                    committees.append(cb["code"])
            elif cls == GROUP_CLASS:
                group_org = org
    except net.FetchError as exc:
        log.error("detail fetch failed for MEP %s (%s): %s", mep_id, person.get("label"), exc)

    group_short = person.get("api:political-group") or ""
    group_label = group_short
    if group_org:
        try:
            group_label = _corporate_body(group_org)["name"]
        except net.FetchError:
            pass

    country_code = person.get("api:country-of-representation") or ""
    slug = _slug(given, family)
    return {
        "id": mep_id,
        "name": person.get("label") or f"{given} {family}".strip(),
        "given_name": given,
        "family_name": family,
        "sort_name": person.get("sortLabel") or normalize.norm_name(f"{family} {given}"),
        "group": group_short,
        "group_label": group_label,
        "country": EU_COUNTRIES.get(country_code, country_code),
        "country_code": country_code,
        "committees": sorted(committees),
        "is_shadow_rapporteur": False,  # filled in by pipeline.run from assignments
        "profile_url": config.MEP_PROFILE_URL.format(mep_id=mep_id),
        "official_meetings_url": config.MEP_MEETINGS_URL.format(mep_id=mep_id, slug=slug),
    }


def fetch_meps() -> tuple[list[dict], dict[str, str], set[str], dict]:
    """Returns (mep_records, committees_map, outgoing_ids, stats)."""
    today_iso = date.today().isoformat()
    current = _list_pages("/meps/show-current", "list_current")
    log.info("current MEPs listed: %d", len(current))

    committees_map: dict[str, str] = {}
    records: list[dict] = []
    failures = 0
    for index, person in enumerate(current, 1):
        rec = _mep_record(person, committees_map, today_iso)
        if not rec["committees"] and not rec["group"]:
            failures += 1
        records.append(rec)
        if index % 100 == 0:
            log.info("MEP details processed: %d/%d", index, len(current))

    if failures > len(records) * 0.1:
        raise net.FetchError(
            f"{failures}/{len(records)} MEP detail documents unusable - aborting"
        )

    # MEPs who left during the term: used to label meetings by departed members.
    outgoing_ids: set[str] = set()
    try:
        outgoing = _list_pages(
            "/meps/show-outgoing", "list_outgoing", {"parliamentary-term": config.TERM}
        )
        outgoing_ids = {str(p.get("identifier")) for p in outgoing}
    except net.FetchError as exc:
        log.warning("show-outgoing unavailable (%s); departed MEPs will be 'unknown'", exc)

    records.sort(key=lambda r: (r["sort_name"], r["id"]))
    stats = {
        "current_meps": len(records),
        "detail_failures": failures,
        "outgoing_meps": len(outgoing_ids),
        "groups": sorted({r["group"] for r in records}),
        "countries": len({r["country_code"] for r in records}),
        "committees": len(committees_map),
    }
    return records, committees_map, outgoing_ids, stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    records, committees_map, outgoing_ids, stats = fetch_meps()
    print("\n=== fetch_meps summary ===")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print(f"  committee codes: {sorted(committees_map)}")
    print(f"  sample: {records[0] if records else None}")


if __name__ == "__main__":
    main()
