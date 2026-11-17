// Deterministic, explainable priority ranking. No network, LLM or randomness in the ranking itself.
//
// Order of an active (incomplete, still submittable, dated) assignment:
//   1. Tier: overdue-but-open, then due within 24h, within 72h, later.
//   2. Local calendar day of the due date (in the viewer's time zone), earliest first.
//   3. Within a day: work due within IMMINENT_HOURS of now stays first, by due time.
//      The rest is ordered by due time, then the slots held by items with an *estimated grade weight*
//      are refilled in descending weight. Items without an estimate never move and are never treated as 0%.
//   4. Ties: due time, course id, assignment id.

export const HIGH_HOURS = 24;
export const MEDIUM_HOURS = 72;
/** Work due this soon is never displaced by a heavier assignment due later the same day. */
export const IMMINENT_HOURS = 3;

export type SubmissionStatus = "submitted" | "graded" | "incomplete" | "unverified" | "unknown" | "excused";
export type PriorityLevel = "high" | "medium" | "low";
export type Section = "active" | "unscheduled" | "not_actionable" | "completed";
export type SortMode = "recommended" | "deadline";

export type PriorityItem = {
  id: number;
  course_id: number;
  course_code: string | null;
  course_name: string | null;
  name: string | null;
  html_url: string | null;
  due_at: string | null;
  lock_at: string | null;
  unlock_at: string | null;
  locked_for_user: boolean;
  lock_unlock_at?: string | null;
  points_possible: number | null;
  submission_types: string[];
  is_group: boolean;
  submission_status: SubmissionStatus;
  submitted_at: string | null;
  missing: boolean;
  late: boolean;
  grade_weight: { percent: number | null; reason: string | null };
};

export type PrioritiesPayload = {
  fetched_at: string;
  cached: boolean;
  courses: { id: number; course_code: string | null; name: string | null }[];
  assignments: PriorityItem[];
  failed_course_ids: number[];
};

export type RankedItem = PriorityItem & {
  section: Section;
  level: PriorityLevel | null;
  overdue: boolean;
  dueMs: number | null;
  /** Why this assignment is in its section / position. */
  explanation: string;
  /** Short status line about completion that Canvas does or does not confirm. */
  statusLabel: string;
};

export type Filters = { courseId: number | "all"; level: PriorityLevel | "all"; showCompleted: boolean };

export type PriorityView = {
  active: RankedItem[];
  unscheduled: RankedItem[];
  notActionable: RankedItem[];
  completed: RankedItem[];
  /** Mutually exclusive: an assignment is counted in at most one of these. */
  counts: { overdue: number; within24h: number; within72h: number; completed: number };
};

// ------------------------------------------------------------------ time helpers
const formatters = new Map<string, Intl.DateTimeFormat>();

export function resolveTimeZone(zone?: string): string {
  const candidate = zone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  try {
    new Intl.DateTimeFormat("en-CA", { timeZone: candidate });
    return candidate;
  } catch {
    return "UTC";
  }
}

/** Whole-day number of the local calendar date in `zone`; differences are calendar-day distances. */
export function localDayNumber(ms: number, zone: string): number {
  let formatter = formatters.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" });
    formatters.set(zone, formatter);
  }
  const parts = Object.fromEntries(formatter.formatToParts(ms).map((part) => [part.type, part.value]));
  return Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)) / 86_400_000;
}

function parseMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;

export function describeDue(dueMs: number, nowMs: number, zone: string): string {
  const hours = (dueMs - nowMs) / 3_600_000;
  if (hours <= 0) {
    const ago = -hours;
    if (ago < 1) return "Overdue by less than an hour";
    return ago < 48 ? `Overdue by ${plural(Math.floor(ago), "hour")}` : `Overdue by ${plural(Math.floor(ago / 24), "day")}`;
  }
  if (hours < 1) return `Due in ${plural(Math.max(1, Math.floor(hours * 60)), "minute")}`;
  if (hours < 24) return `Due in ${plural(Math.floor(hours), "hour")}`;
  const days = localDayNumber(dueMs, zone) - localDayNumber(nowMs, zone);
  return days === 1 ? "Due tomorrow" : `Due in ${plural(days, "day")}`;
}

// ------------------------------------------------------------------ classification
type Classified = { item: PriorityItem; section: Section; dueMs: number | null; hours: number | null; note: string };

function classify(item: PriorityItem, nowMs: number): Classified {
  const dueMs = parseMs(item.due_at);
  const base = { item, dueMs, hours: dueMs === null ? null : (dueMs - nowMs) / 3_600_000 };
  if (item.submission_status === "submitted") return { ...base, section: "completed", note: "Submitted" };
  if (item.submission_status === "graded") return { ...base, section: "completed", note: "Graded" };
  if (item.submission_status === "excused") return { ...base, section: "not_actionable", note: "Excused" };
  const unlockMs = parseMs(item.unlock_at) ?? parseMs(item.lock_unlock_at);
  if (unlockMs !== null && unlockMs > nowMs) return { ...base, section: "not_actionable", note: "Not available yet" };
  const lockMs = parseMs(item.lock_at);
  if ((lockMs !== null && lockMs <= nowMs) || item.locked_for_user) {
    return { ...base, section: "not_actionable", note: "Closed — Canvas is not accepting submissions" };
  }
  return { ...base, section: dueMs === null ? "unscheduled" : "active", note: "" };
}

const STATUS_LABELS: Record<SubmissionStatus, string> = {
  submitted: "Submitted",
  graded: "Graded",
  excused: "Excused",
  incomplete: "Not submitted",
  unverified: "Not tracked online — submission unverified",
  unknown: "Submission status unavailable",
};

function statusLabel(item: PriorityItem): string {
  const label = STATUS_LABELS[item.submission_status] ?? STATUS_LABELS.unknown;
  return item.missing && item.submission_status === "incomplete" ? "Marked missing in Canvas" : label;
}

// ------------------------------------------------------------------ ordering of active items
function stable(a: Classified, b: Classified): number {
  return (a.dueMs ?? 0) - (b.dueMs ?? 0) || a.item.course_id - b.item.course_id || a.item.id - b.item.id;
}

const weightOf = (c: Classified) => c.item.grade_weight?.percent ?? null;

function tierOf(c: Classified): number {
  const hours = c.hours as number;
  return hours <= 0 ? 0 : hours <= HIGH_HOURS ? 1 : hours <= MEDIUM_HOURS ? 2 : 3;
}

type Ordered = { entry: Classified; weightNote: string };

function orderActive(entries: Classified[], nowMs: number, zone: string): Ordered[] {
  const result: Ordered[] = [];
  const overdue = entries.filter((e) => tierOf(e) === 0).sort((a, b) => (b.dueMs! - a.dueMs!) || stable(a, b));
  overdue.forEach((entry) => result.push({ entry, weightNote: "" }));

  for (const tier of [1, 2, 3]) {
    const inTier = entries.filter((e) => tierOf(e) === tier);
    const days = [...new Set(inTier.map((e) => localDayNumber(e.dueMs!, zone)))].sort((a, b) => a - b);
    for (const day of days) {
      const sameDay = inTier.filter((e) => localDayNumber(e.dueMs!, zone) === day).sort(stable);
      const imminent = sameDay.filter((e) => e.hours! <= IMMINENT_HOURS);
      const rest = sameDay.filter((e) => e.hours! > IMMINENT_HOURS);
      const slots = rest.map((e, index) => (weightOf(e) === null ? -1 : index)).filter((index) => index >= 0);
      const weighted = slots.map((index) => rest[index])
        .sort((a, b) => (weightOf(b)! - weightOf(a)!) || stable(a, b));
      const arranged = [...rest];
      slots.forEach((slot, i) => { arranged[slot] = weighted[i]; });
      imminent.forEach((entry) => result.push({ entry, weightNote: "" }));
      arranged.forEach((entry, position) => {
        const earlierDueBehind = arranged.slice(position + 1).some((o) => o.dueMs! < entry.dueMs!);
        const laterDueAhead = arranged.slice(0, position).some((o) => o.dueMs! > entry.dueMs!);
        const weightNote = earlierDueBehind
          ? "Ranked above earlier work due the same day because of higher estimated grade weight."
          : laterDueAhead ? "Ranked after heavier-weighted work due the same day." : "";
        result.push({ entry, weightNote });
      });
    }
  }
  return result;
}

// ------------------------------------------------------------------ public API
function toRanked(c: Classified, nowMs: number, zone: string, weightNote = ""): RankedItem {
  const { item, section, dueMs, hours } = c;
  let level: PriorityLevel | null = null;
  let explanation = c.note;
  const overdue = section === "active" && hours !== null && hours <= 0;
  if (section === "active" && hours !== null && dueMs !== null) {
    level = overdue || hours <= HIGH_HOURS ? "high" : hours <= MEDIUM_HOURS ? "medium" : "low";
    const label = level === "high" ? "High" : level === "medium" ? "Medium" : "Low";
    const timing = describeDue(dueMs, nowMs, zone);
    explanation = `${label} priority — ${timing}${overdue ? "; Canvas still accepts submissions" : ""}.`;
    if (weightNote) explanation += ` ${weightNote}`;
    else if (typeof item.grade_weight?.percent !== "number") explanation += " Grade weight unavailable — ranked by deadline.";
  } else if (section === "unscheduled") {
    explanation = "No due date in Canvas — not ranked by urgency.";
  } else if (section === "not_actionable") {
    explanation = `${c.note}.`;
  } else if (section === "completed") {
    explanation = `${c.note} — no action needed.`;
  }
  return { ...item, section, level, overdue, dueMs, explanation, statusLabel: statusLabel(item) };
}

const byName = (a: RankedItem, b: RankedItem) =>
  (a.course_code ?? "").localeCompare(b.course_code ?? "") || (a.name ?? "").localeCompare(b.name ?? "") || a.id - b.id;

/** Rank everything, then apply the viewer's filters and sort mode. `now` and `zone` are injectable for tests. */
export function buildPriorityView(
  items: PriorityItem[],
  nowMs: number,
  filters: Filters = { courseId: "all", level: "all", showCompleted: true },
  sort: SortMode = "recommended",
  timeZone?: string,
): PriorityView {
  const zone = resolveTimeZone(timeZone);
  const scoped = filters.courseId === "all" ? items : items.filter((i) => i.course_id === filters.courseId);
  const classified = scoped.map((item) => classify(item, nowMs));
  const activeEntries = classified.filter((c) => c.section === "active");

  let active: RankedItem[];
  if (sort === "deadline") {
    active = activeEntries.sort(stable).map((c) => toRanked(c, nowMs, zone));
  } else {
    active = orderActive(activeEntries, nowMs, zone).map(({ entry, weightNote }) => toRanked(entry, nowMs, zone, weightNote));
  }
  const others = (section: Section, compare: (a: RankedItem, b: RankedItem) => number) =>
    classified.filter((c) => c.section === section).map((c) => toRanked(c, nowMs, zone)).sort(compare);
  const byDue = (a: RankedItem, b: RankedItem) =>
    (a.dueMs ?? Number.MAX_SAFE_INTEGER) - (b.dueMs ?? Number.MAX_SAFE_INTEGER) || byName(a, b);
  const unscheduled = others("unscheduled", byName);
  const notActionable = others("not_actionable", byDue);
  const completed = others("completed", (a, b) => (b.dueMs ?? 0) - (a.dueMs ?? 0) || byName(a, b));

  const counts = {
    overdue: active.filter((i) => i.overdue).length,
    within24h: active.filter((i) => !i.overdue && i.level === "high").length,
    within72h: active.filter((i) => i.level === "medium").length,
    completed: completed.length,
  };
  const levelOnly = filters.level !== "all";
  return {
    active: levelOnly ? active.filter((i) => i.level === filters.level) : active,
    unscheduled: levelOnly ? [] : unscheduled,
    notActionable: levelOnly ? [] : notActionable,
    completed: levelOnly || !filters.showCompleted ? [] : completed,
    counts,
  };
}

export function formatGradeWeight(weight: PriorityItem["grade_weight"] | undefined): string {
  const percent = weight?.percent;
  return typeof percent === "number" && Number.isFinite(percent) ? `~${percent}%` : "Unavailable";
}

// ------------------------------------------------------------------ data access
export async function getPriorities(options: { refresh?: boolean; signal?: AbortSignal } = {}): Promise<PrioritiesPayload> {
  const query = options.refresh ? "?refresh=true" : "";
  const response = await fetch(`http://127.0.0.1:8000/api/priorities${query}`, { signal: options.signal, cache: "no-store" });
  if (!response.ok) throw new Error(`Failed to fetch priorities: HTTP ${response.status}`);
  const data = await response.json();
  if (!data || !Array.isArray(data.assignments) || !Array.isArray(data.courses) || !Array.isArray(data.failed_course_ids)) {
    throw new Error("Invalid priorities response");
  }
  return data as PrioritiesPayload;
}
