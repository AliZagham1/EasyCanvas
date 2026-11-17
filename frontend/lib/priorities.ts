// Deterministic deadline-based priorities. No network, LLM or randomness in the ranking itself.
//
// An assignment is *active* when it is incomplete, still submittable and has a due date:
//   Overdue  due date has passed (own section, most recently overdue first; labeled High)
//   High     due within 24 hours
//   Medium   due within 72 hours
//   Low      due later
// Active upcoming work is sorted by earliest deadline. Everything else is separated, never guessed:
// no due date -> Unscheduled; submitted/graded -> Completed; excused, closed, locked or not yet open -> Not actionable.

export const HIGH_HOURS = 24;
export const MEDIUM_HOURS = 72;

export type SubmissionStatus = "submitted" | "graded" | "incomplete" | "unverified" | "unknown" | "excused";
export type PriorityLevel = "high" | "medium" | "low";
export type Section = "overdue" | "upcoming" | "unscheduled" | "not_actionable" | "completed";
export type SortMode = "due" | "course";

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
  /** Short deadline text ("Due in 8 hours", "Overdue by 2 days") or why it is not actionable. */
  dueLabel: string;
  /** Completion as Canvas reports it; unverifiable work is labeled, never marked done. */
  statusLabel: string;
};

export type Filters = { courseId: number | "all"; level: PriorityLevel | "all"; showCompleted: boolean };

export type PriorityView = {
  overdue: RankedItem[];
  upcoming: RankedItem[];
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
const STATUS_LABELS: Record<SubmissionStatus, string> = {
  submitted: "Submitted",
  graded: "Graded",
  excused: "Excused",
  incomplete: "Not submitted",
  unverified: "Not tracked online",
  unknown: "Status unavailable",
};

function statusLabel(item: PriorityItem): string {
  if (item.missing && item.submission_status === "incomplete") return "Missing";
  return STATUS_LABELS[item.submission_status] ?? STATUS_LABELS.unknown;
}

function classify(item: PriorityItem, nowMs: number): { section: Section; note: string } {
  if (item.submission_status === "submitted" || item.submission_status === "graded") return { section: "completed", note: "" };
  if (item.submission_status === "excused") return { section: "not_actionable", note: "" };
  const unlockMs = parseMs(item.unlock_at) ?? parseMs(item.lock_unlock_at);
  if (unlockMs !== null && unlockMs > nowMs) return { section: "not_actionable", note: "Not open yet" };
  const lockMs = parseMs(item.lock_at);
  if (lockMs !== null && lockMs <= nowMs) return { section: "not_actionable", note: "Closed" };
  if (item.locked_for_user) return { section: "not_actionable", note: "Locked" };
  const dueMs = parseMs(item.due_at);
  if (dueMs === null) return { section: "unscheduled", note: "" };
  return { section: dueMs <= nowMs ? "overdue" : "upcoming", note: "" };
}

function rank(item: PriorityItem, nowMs: number, zone: string): RankedItem {
  const { section, note } = classify(item, nowMs);
  const dueMs = parseMs(item.due_at);
  let level: PriorityLevel | null = null;
  let dueLabel = note;
  if ((section === "overdue" || section === "upcoming") && dueMs !== null) {
    const hours = (dueMs - nowMs) / 3_600_000;
    level = hours <= HIGH_HOURS ? "high" : hours <= MEDIUM_HOURS ? "medium" : "low";
    dueLabel = describeDue(dueMs, nowMs, zone);
  }
  return { ...item, section, level, overdue: section === "overdue", dueMs, dueLabel, statusLabel: statusLabel(item) };
}

// ------------------------------------------------------------------ public API
const FAR = Number.MAX_SAFE_INTEGER;
const byIds = (a: RankedItem, b: RankedItem) => a.course_id - b.course_id || a.id - b.id;
const byCourse = (a: RankedItem, b: RankedItem) =>
  (a.course_code ?? "").localeCompare(b.course_code ?? "") || a.course_id - b.course_id;
const byName = (a: RankedItem, b: RankedItem) => byCourse(a, b) || (a.name ?? "").localeCompare(b.name ?? "") || a.id - b.id;
const dueAsc = (a: RankedItem, b: RankedItem) => (a.dueMs ?? FAR) - (b.dueMs ?? FAR) || byIds(a, b);
/** Most recently overdue first, so very old overdue work does not push everything else down. */
const dueDesc = (a: RankedItem, b: RankedItem) => (b.dueMs ?? 0) - (a.dueMs ?? 0) || byIds(a, b);

/** Classify, filter and sort. `now` and `timeZone` are injectable so behavior is testable. */
export function buildPriorityView(
  items: PriorityItem[],
  nowMs: number,
  filters: Filters = { courseId: "all", level: "all", showCompleted: true },
  sort: SortMode = "due",
  timeZone?: string,
): PriorityView {
  const zone = resolveTimeZone(timeZone);
  const scoped = filters.courseId === "all" ? items : items.filter((i) => i.course_id === filters.courseId);
  const ranked = scoped.map((item) => rank(item, nowMs, zone));
  const pick = (section: Section, compare: (a: RankedItem, b: RankedItem) => number) => {
    const list = ranked.filter((i) => i.section === section);
    return sort === "course" ? list.sort((a, b) => byCourse(a, b) || compare(a, b)) : list.sort(compare);
  };
  const overdue = pick("overdue", dueDesc);
  const upcoming = pick("upcoming", dueAsc);
  const unscheduled = ranked.filter((i) => i.section === "unscheduled").sort(byName);
  const notActionable = pick("not_actionable", dueAsc);
  const completed = pick("completed", dueDesc);

  const counts = {
    overdue: overdue.length,
    within24h: upcoming.filter((i) => i.level === "high").length,
    within72h: upcoming.filter((i) => i.level === "medium").length,
    completed: completed.length,
  };
  const byLevel = filters.level !== "all";
  const matches = (i: RankedItem) => i.level === filters.level;
  return {
    overdue: byLevel ? overdue.filter(matches) : overdue, // overdue work counts as High
    upcoming: byLevel ? upcoming.filter(matches) : upcoming,
    unscheduled: byLevel ? [] : unscheduled,
    notActionable: byLevel ? [] : notActionable,
    completed: byLevel || !filters.showCompleted ? [] : completed,
    counts,
  };
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
