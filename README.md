# MEP of the Week — mpoftheweek.com

A public, static website tracking **how transparently Members of the European Parliament
disclose their meetings with interest representatives** ("lobbyists"), with a special focus
on shadow rapporteurs. Built entirely on official European Parliament data.

Two switchable views over the same population of current MEPs:

| View | Question it answers | Confidence |
|---|---|---|
| **A — Disclosure volume** | How many lobby meetings has each MEP published this term? | High (direct from the official register) |
| **B — Shadow-rapporteur compliance** | For each shadow rapporteur, how many of their files have at least one related meeting on record? | **Lower** (procedure-code matching only — clearly labelled on-site) |

The homepage ranks both views best→worst and worst→best, auto-generates a deterministic
weekly **"MEP of the Week"** (top of board, 8-week rotation) and a **Watchlist**
(bottom of View B, restricted to MEPs shadowing ≥3 files).

> **Framing rule:** this is a factual, civic accountability tool, not a smear site. Absence
> of declared meetings is *not* proof of wrongdoing. All copy stays neutral, every figure
> links to the official EP source, and the Methodology page lists every caveat.

## Architecture

```
pipeline/   Python 3.11+ (requests only) — fetch, normalize, compute, write JSON
site/       Astro + TypeScript static site — reads site/src/data/*.json at build time
.github/    weekly refresh workflow (pipeline → tests → build → deploy → commit data)
overrides.json  manual curation (featured MEP, watchlist exclusions) without code changes
```

No backend, no database, no client-side storage; sort/filter/search run in vanilla JS over
prebuilt JSON, state lives in URL query params.

## Data sources (all official, verified live)

1. **Declared meetings** — the EP ["Search MEP meetings"](https://www.europarl.europa.eu/meps/en/search-meetings)
   CSV export (`fromDate`/`toDate` as `dd/MM/yyyy`). Columns:
   `title, member_id, member_name, meeting_date, member_capacity, procedure_reference, attendees, lobbyist_id`.
   ⚠️ The export caps at **1,000 rows per query** (sorted date-descending; earliest rows are
   silently dropped, pagination parameters are ignored — verified empirically, the often-cited
   10k limit is wrong). `fetch_meetings.py` therefore bisects date windows adaptively and
   flags any single day that ever hits the cap. `member_id` joins records directly to the
   Open Data API — no name matching needed.
2. **MEP register** — [EP Open Data API v2](https://data.europarl.europa.eu/) (CC BY 4.0):
   `/meps/show-current` (id, name, country, political group — groups are read from the API,
   never hardcoded), `/meps/{id}` (dated committee/group memberships),
   `/corporate-bodies/{id}` (committee codes/names), `/meps/show-outgoing` (departures).
   ⚠️ The API intermittently returns **HTTP 200 with an `{"error": …}` body** under load;
   `net.py` detects and retries this. Rate limit 500 req/5 min — the pipeline throttles to
   ~0.7 s between calls.
3. **Shadow-rapporteur assignments** — `/procedures?year=YYYY` + `/procedures/{id}`:
   `had_participation[]` entries with `participation_role` `RAPPORTEUR_SHADOW` /
   `RAPPORTEUR_SHADOW_OPINION` carry the person id, political group, appointment date and —
   crucially — `parliamentary_term: "org/ep-10"`, which scopes assignments to the current
   term even on files carried over from earlier years. Years 2021→present are scanned
   (configurable in `config.py`). OEIL procedure pages and Parltrack dumps remain documented
   fallbacks; the official API proved to cover the full denominator, so they are unused.
   This source is **decoupled**: if it fails, the site degrades gracefully to View A only.

Raw responses are cached under `pipeline/data/raw/` (gitignored) so re-runs are debuggable
and offline-friendly. `--no-cache` forces refetching; recent meeting windows auto-expire.

## Quickstart

```bash
make setup            # python3.12 venv + pip deps + npm install
make refresh          # python -m pipeline.run  +  astro build
make dev              # local dev server on http://localhost:4321
make test             # pytest unit suite
```

Or directly:

```bash
.venv/bin/python -m pipeline.run [--no-cache] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--skip-assignments]
cd site && npm run dev
```

First full pull takes ~60 min (dominated by ~4,000 polite procedure-detail requests);
subsequent runs reuse the cache. The run ends with a summary: counts, date coverage,
truncation warnings, unmatched declarants, weekly picks.

## Build inputs written to `site/src/data/`

- `meta.json` — generated_at, term, date range, source URLs, counts, unmatched names, warnings.
- `meps.json` — one record per current MEP (id, name, group + full label, country,
  committees, `is_shadow_rapporteur`, official URLs).
- `meetings.json` — all normalized meetings (one JSON object per line for clean git diffs).
- `assignments.json` — (procedure ↔ MEP) shadow-rapporteur pairs with role, committee,
  appointment date.
- `rankings.json` — precomputed View A/B rows + ranks, weekly feature picks, watchlist,
  committee code→name map.
- `weekly_history.json` — past weekly picks (drives the 8-week rotation; a week's pick is
  frozen once recorded).

All output is deterministic (sorted keys, stable ordering) — identical inputs give
byte-identical files.

## Metrics (full definitions on the site's Methodology page)

- **View A:** meetings_total, split by capacity (shadow rapporteur / rapporteur / committee
  chair / member / other), distinct organisations, share with a Transparency-Register-linked
  counterpart, first/last meeting date. Default sort: meetings_total desc.
- **View B:** files_shadowed, files_with_related_meeting (matching the MEP's meetings by
  procedure code, **any** capacity), coverage %, shadow-capacity meeting count.
  Ties break deterministically (matched files → shadow meetings → name → id).
- **Weekly picks:** computed from data + ISO week only. Rotation: nobody featured twice in
  8 weeks. Watchlist: bottom 5 of View B with ≥3 files shadowed.

### Manual curation — `overrides.json`

```json
{
  "featured_mep_id": null,        // force the MEP of the Week (labelled "editor's pick")
  "watchlist_exclude_ids": [],    // keep specific MEPs off the Watchlist
  "notes": "..."
}
```

## Deployment (Cloudflare Pages) & weekly automation

`.github/workflows/refresh.yml` runs every **Monday 06:00 UTC** (plus manual
`workflow_dispatch`): pipeline → tests → `astro build` → deploy → commit refreshed JSON.

One-time setup:

1. Create a Cloudflare Pages project named `mpoftheweek` (Workers & Pages → Create → Pages).
2. Add repo secrets (Settings → Secrets and variables → Actions):
   - `CLOUDFLARE_API_TOKEN` — API token with the *Cloudflare Pages — Edit* permission.
   - `CLOUDFLARE_ACCOUNT_ID` — from the Cloudflare dashboard sidebar.
3. Point `mpoftheweek.com` at the Pages project (Custom domains tab).

Netlify or GitHub Pages work too — replace the deploy step (`site/dist` is plain static
output). Update `REPO_URL` in `site/src/lib/data.ts` and the contact address in
`pipeline/config.py` / `data.ts` if you fork this.

## Caveats that govern interpretation (also on the site)

- Absence of declared meetings ≠ wrongdoing; declarations are self-reported and unpoliced.
- View B matching misses meetings declared as free text without a procedure code.
- More meetings = more disclosure, which can also reflect more lobbying access.
- The CSV export exposes no meeting place or committee columns (despite older docs).
- MEPs who left mid-term are excluded from rankings; their meetings stay in the dataset and
  are listed in `meta.json` under `unmatched_names`.
- Scope is strictly the 10th term (from 16 July 2024); the March 2022 source format change
  is therefore irrelevant here.

## Licence & attribution

Source data © European Union / European Parliament, reused under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Independent project — not
affiliated with the European Parliament. See also Transparency International EU's
[Integrity Watch](https://www.integritywatch.eu/mepmeetings) for a related view of the same
declarations.
