"""Central configuration for the MEP of the Week data pipeline."""
from __future__ import annotations

from datetime import date
from pathlib import Path

# --- Paths -------------------------------------------------------------------
PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent
RAW_DIR = PIPELINE_DIR / "data" / "raw"
SITE_DATA_DIR = REPO_ROOT / "site" / "src" / "data"
OVERRIDES_PATH = REPO_ROOT / "overrides.json"

# --- Scope: 10th parliamentary term only --------------------------------------
TERM = 10
TERM_LABEL = "10th parliamentary term (2024–2029)"
TERM_START = date(2024, 7, 16)
# Value of `parliamentary_term` on EP Open Data API participations.
TERM_ORG_ID = "org/ep-10"

# Procedures registered in these years are scanned for term-10 shadow
# appointments. Carried-over files older than 2021 with fresh term-10
# appointments are vanishingly rare; widen the window here if ever needed.
ASSIGNMENT_SCAN_YEARS = list(range(2021, date.today().year + 1))

# --- Endpoints -----------------------------------------------------------------
EP_API_BASE = "https://data.europarl.europa.eu/api/v2"
EP_API_FORMAT = "application/ld+json"
# Official "search MEP meetings" tool (CSV export).
MEETINGS_EXPORT_URL = "https://www.europarl.europa.eu/meps/en/search-meetings"
# Verified empirically 2026-06: the CSV export caps at 1,000 rows per query
# (sorted by date descending - the EARLIEST rows are dropped), and ignores all
# pagination parameters. Windows that hit the cap must be bisected.
MEETINGS_EXPORT_ROW_CAP = 1000

OEIL_PROCEDURE_URL = "https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference={ref}"
# Verified live 2026-06 (HTTP 200): the spec'd format works.
TRANSPARENCY_REGISTER_URL = "https://transparency-register.europa.eu/search-details_en?id={tr_id}"
MEP_PROFILE_URL = "https://www.europarl.europa.eu/meps/en/{mep_id}"
# {slug} = GIVENNAME_FAMILYNAME, uppercased and accent-stripped; the server
# 301-corrects any slug as long as the numeric id is right.
MEP_MEETINGS_URL = "https://www.europarl.europa.eu/meps/en/{mep_id}/{slug}/meetings/past"

# --- Networking ---------------------------------------------------------------
USER_AGENT = (
    "MEPoftheWeek/1.0 (civic lobbying-transparency tracker; "
    "https://mpoftheweek.com; contact: busterfranken@gmail.com)"
)
TIMEOUT = 90
RETRIES = 4
BACKOFF_BASE = 5.0  # seconds; doubled per retry, with jitter
# data.europarl.europa.eu allows 500 requests / 5 min; stay well under.
API_MIN_INTERVAL = 0.7
# Politeness towards www.europarl.europa.eu (no published limit).
WWW_MIN_INTERVAL = 1.0

# --- Cache TTLs ----------------------------------------------------------------
MEP_DETAIL_TTL_DAYS = 3
PROCEDURE_DETAIL_TTL_DAYS = 6
PROCEDURE_LIST_CURRENT_YEAR_TTL_DAYS = 1
# Meeting windows ending within this many days of today are considered "hot"
# (declarations are still being added) and re-fetched after a short TTL.
MEETINGS_HOT_WINDOW_DAYS = 14
MEETINGS_HOT_TTL_HOURS = 12

# --- Weekly picks ----------------------------------------------------------------
WATCHLIST_MIN_FILES_SHADOWED = 3
WATCHLIST_SIZE = 5
FEATURE_ROTATION_WEEKS = 8
