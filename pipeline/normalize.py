"""Normalization helpers: names, procedure codes, organisations, dedupe keys."""
from __future__ import annotations

import re
import unicodedata

# --- Names ---------------------------------------------------------------------


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def norm_name(name: str) -> str:
    """Uppercase, accent-stripped, punctuation-free key for joining MEP names."""
    s = strip_accents(name or "").upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return " ".join(s.split())


# --- Procedure codes -------------------------------------------------------------
# Meetings CSV / OEIL display style: 2023/0448(COD)
# EP Open Data API process_id style:  2023-0448
PROC_REF_RE = re.compile(r"\b(\d{4})\s*/\s*(\d{4})\s*\(\s*([A-Z]{2,5})\s*\)")
PROC_KEY_RE = re.compile(r"^(\d{4})-(\d{4})$")


def extract_procedure_refs(text: str) -> list[tuple[str, str]]:
    """All (display, key) procedure references found in free text, in order.

    >>> extract_procedure_refs("On 2023/0448(COD) and 2024/2005(INI)")
    [('2023/0448(COD)', '2023-0448'), ('2024/2005(INI)', '2024-2005')]
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in PROC_REF_RE.finditer(text or ""):
        display = f"{m.group(1)}/{m.group(2)}({m.group(3)})"
        key = f"{m.group(1)}-{m.group(2)}"
        if key not in seen:
            seen.add(key)
            out.append((display, key))
    return out


def proc_display_to_key(display: str) -> str | None:
    """'2023/0448(COD)' -> '2023-0448' (None when no reference is present)."""
    refs = extract_procedure_refs(display)
    return refs[0][1] if refs else None


def proc_key_to_display(key: str, proc_type: str | None = None) -> str:
    """'2023-0448' (+ 'COD') -> '2023/0448(COD)'; echoes invalid keys back."""
    m = PROC_KEY_RE.match(key or "")
    if not m:
        return key
    base = f"{m.group(1)}/{m.group(2)}"
    return f"{base}({proc_type})" if proc_type else base


# --- Meeting capacities ------------------------------------------------------------
# Buckets used by View A metrics. Raw values observed in the CSV export include
# "Member", "Committee chair", "Rapporteur", "Shadow rapporteur"; staff-held
# meetings and any future labels fall through to "other".


def capacity_bucket(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return "other"
    if "shadow" in s:
        return "shadow_rapporteur"
    if "rapporteur" in s:
        return "rapporteur"
    if "chair" in s and "committee" in s:
        return "committee_chair"
    if s == "member":
        return "member"
    return "other"


# --- Organisations ------------------------------------------------------------------


def clean_org(raw: str) -> str:
    """Trim and collapse whitespace; keep the original wording otherwise."""
    return " ".join((raw or "").split())


# --- Meetings -----------------------------------------------------------------------


def meeting_dedupe_key(m: dict) -> tuple:
    """Identity of a declared meeting: (MEP, date, organisation, procedure, subject)."""
    return (
        m.get("mep_id") or norm_name(m.get("mep_name", "")),
        m.get("date", ""),
        norm_name(m.get("attendees", "")),
        tuple(m.get("procedure_keys") or ()),
        norm_name(m.get("title", "")),
        capacity_bucket(m.get("capacity", "")),
    )
