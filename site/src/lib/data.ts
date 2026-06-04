/**
 * Typed access to the prebuilt JSON written by `python -m pipeline.run`.
 * All joins happen once at build time (this module is only imported by
 * prerendered pages); nothing here ships to the client.
 */
import mepsRaw from '../data/meps.json';
import meetingsRaw from '../data/meetings.json';
import assignmentsRaw from '../data/assignments.json';
import rankingsRaw from '../data/rankings.json';
import metaRaw from '../data/meta.json';

export interface Mep {
  id: string;
  name: string;
  given_name: string;
  family_name: string;
  sort_name: string;
  group: string;
  group_label: string;
  country: string;
  country_code: string;
  committees: string[];
  is_shadow_rapporteur: boolean;
  profile_url: string;
  official_meetings_url: string;
}

export interface Meeting {
  mep_id: string | null;
  mep_name: string;
  date: string;
  capacity: string;
  capacity_bucket: string;
  title: string;
  procedure_code: string | null;
  procedure_keys: string[];
  attendees: string;
  registered: boolean;
  tr_ids: string[];
}

export interface Assignment {
  procedure_key: string;
  procedure_code: string;
  procedure_title: string | null;
  procedure_type: string | null;
  committee: string | null;
  mep_id: string;
  role: 'shadow_rapporteur' | 'shadow_rapporteur_opinion';
  group_at_appointment: string | null;
  appointed: string | null;
}

export interface ViewARow {
  mep_id: string;
  rank: number;
  meetings_total: number;
  meetings_by_capacity: Record<string, number>;
  distinct_organisations: number;
  share_registered: number | null;
  first_meeting_date: string | null;
  last_meeting_date: string | null;
}

export interface ViewBRow {
  mep_id: string;
  rank: number;
  files_shadowed: number;
  files_with_related_meeting: number;
  coverage_pct: number;
  shadow_meetings_total: number;
}

export interface Feature {
  mep_id: string;
  source: 'auto' | 'history' | 'override';
  week: string;
}

export interface Rankings {
  week: string;
  view_a: { rows: ViewARow[] };
  view_b: { available: boolean; rows: ViewBRow[] };
  weekly: { feature_a: Feature | null; feature_b: Feature | null; watchlist: string[] };
  committees: Record<string, string>;
}

export interface Meta {
  generated_at: string;
  term: number;
  term_label: string;
  date_range: { from: string; to: string };
  source_urls: Record<string, string>;
  licence: string;
  counts: Record<string, number>;
  view_b: { available: boolean; reason: string };
  rules: { feature_rotation_weeks: number; watchlist_min_files_shadowed: number; watchlist_size: number };
  unmatched_names: { mep_id: string | null; name: string; meetings: number; status: string }[];
  warnings: Record<string, unknown>;
}

export const meps = mepsRaw as Mep[];
export const meetings = meetingsRaw as Meeting[];
export const assignments = assignmentsRaw as Assignment[];
export const rankings = rankingsRaw as unknown as Rankings;
export const meta = metaRaw as unknown as Meta;

export const mepsById = new Map(meps.map((m) => [m.id, m]));
export const viewAById = new Map(rankings.view_a.rows.map((r) => [r.mep_id, r]));
export const viewBById = new Map(rankings.view_b.rows.map((r) => [r.mep_id, r]));

function groupBy<T>(items: T[], key: (item: T) => string | null): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const item of items) {
    const k = key(item);
    if (!k) continue;
    const bucket = out.get(k);
    if (bucket) bucket.push(item);
    else out.set(k, [item]);
  }
  return out;
}

export const meetingsByMep = groupBy(meetings, (m) => m.mep_id);
export const assignmentsByMep = groupBy(assignments, (a) => a.mep_id);

/** Procedure keys for which a given MEP has at least one declared meeting. */
export function metProcedureKeys(mepId: string): Set<string> {
  const keys = new Set<string>();
  for (const m of meetingsByMep.get(mepId) ?? []) {
    for (const k of m.procedure_keys) keys.add(k);
  }
  return keys;
}

// ---------- External link builders ----------

export const oeilUrl = (procedureCode: string) =>
  `https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference=${encodeURIComponent(procedureCode)}`;

export const registerUrl = (trId: string) =>
  `https://transparency-register.europa.eu/search-details_en?id=${encodeURIComponent(trId)}`;

// ---------- Formatting ----------

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

export function fmtShare(x: number | null | undefined): string {
  return x == null ? '—' : `${Math.round(x * 100)}%`;
}

export function fmtPct(x: number | null | undefined): string {
  return x == null ? '—' : `${x}%`;
}

/** Conventional political-group colours, matched tolerantly against the
 *  API-provided short label; unknown groups get a neutral fallback so a new
 *  or renamed group degrades gracefully (the list is never hardcoded). */
export function groupColor(short: string): string {
  const s = (short || '').toUpperCase();
  if (/(^|\b)(EPP|PPE)\b/.test(s)) return '#3a7bd5';
  if (/S&D|S-D|SD/.test(s)) return '#d4373e';
  if (/PATRIOT|PFE|PF E/.test(s)) return '#3b3470';
  if (/ECR/.test(s)) return '#0f6f8e';
  if (/RENEW/.test(s)) return '#c79a00';
  if (/GREEN|VERT|EFA/.test(s)) return '#3f8b3f';
  if (/LEFT|GUE/.test(s)) return '#a02a2a';
  if (/ESN|SOVEREIGN/.test(s)) return '#54442b';
  if (/NI|NON/.test(s)) return '#6b7682';
  return '#6b7682';
}

export const REPORT_ERROR_URL =
  'mailto:busterfranken@gmail.com?subject=MEP%20of%20the%20Week%20%E2%80%94%20data%20correction';
export const REPO_URL = 'https://github.com/busterfranken/mpoftheweek';
