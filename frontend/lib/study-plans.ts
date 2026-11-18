// Study plans (no AI): a student's own steps for an assignment or exam, and times the backend finds in their free hours.
// Deadlines come from Canvas (assignments) or from the student (exams); times are proposed, then saved only when accepted.

export type PlanStep = {
  activity_id: string; plan_id: string; position: number; title: string; minutes: number; done: boolean;
  start_at: string | null; end_at: string | null;
};
export type StudyPlan = {
  plan_id: string; course_id: number; kind: "assignment" | "custom"; assignment_id: number | null; title: string; due_at: string;
  course_code: string | null; course_name: string | null; time_zone: string | null; activities: PlanStep[];
  needed_minutes: number; scheduled_minutes: number; done_minutes: number;
};
export type PlanPreferences = { day_start: string; day_end: string; max_minutes_per_day: number; buffer_minutes: number };
export type PlansPayload = { plans: StudyPlan[]; preferences: PlanPreferences };
export type Placement = { activity_id: string; start_at: string; end_at: string };
export type Proposal = {
  plan_id: string; placements: Placement[]; unplaced: { activity_id: string; reason: string }[];
  needed_minutes: number; free_minutes: number; past_deadline: boolean;
};
export type NewPlanInput = { course_id: number; assignment_id?: number; title?: string; due_at?: string };

/** What a chosen time overlaps: a class or study block (they stay put) or another study session (replaced if the student adds anyway). */
export type Conflict = { kind: "class" | "study_block" | "session"; title: string; when: string; activity_id?: string };
export type ChosenTime = { activity: PlanStep; replaced: { activity_id: string; title: string }[]; overlaps: Conflict[] };

/** The chosen time is booked (HTTP 409). Carries what is there, so the student can decide to add it anyway. */
export class TimeConflict extends Error {
  conflicts: Conflict[];
  constructor(message: string, conflicts: Conflict[]) { super(message); this.name = "TimeConflict"; this.conflicts = conflicts; }
}

export const STEP_LENGTHS = [15, 30, 45, 60, 90, 120, 180];

const base = "http://127.0.0.1:8000/api/study-plans";
async function call<T>(path = "", init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await fetch(base + path, { cache: "no-store", signal, ...init, headers: init?.body ? { "Content-Type": "application/json" } : undefined });
  if (!response.ok) {
    let detail = "";
    try {
      const data = await response.json();
      if (response.status === 409 && Array.isArray(data?.conflicts)) throw new TimeConflict(typeof data.detail === "string" ? data.detail : "You already have something at this time.", data.conflicts);
      if (typeof data?.detail === "string") detail = data.detail;
      else if (Array.isArray(data?.detail)) detail = "Check the values you entered and try again.";
    } catch (error) { if (error instanceof TimeConflict) throw error; /* otherwise keep the generic message */ }
    throw new Error(detail || "The study plan request failed. Check the backend and retry.");
  }
  return response.json();
}
const body = (value: unknown) => JSON.stringify(value);

export const getPlans = (signal?: AbortSignal) => call<PlansPayload>("", undefined, signal);
export const createPlan = (input: NewPlanInput) => call<StudyPlan>("", { method: "POST", body: body(input) });
export const deletePlan = (id: string) => call<{ ok: true }>(`/${id}`, { method: "DELETE" });
export const addStep = (id: string, input: { title: string; minutes: number }) => call<PlanStep>(`/${id}/activities`, { method: "POST", body: body(input) });
export const updateStep = (id: string, stepId: string, changes: Partial<Pick<PlanStep, "title" | "minutes" | "done">>) =>
  call<PlanStep>(`/${id}/activities/${stepId}`, { method: "PATCH", body: body(changes) });
export const deleteStep = (id: string, stepId: string) => call<{ ok: true }>(`/${id}/activities/${stepId}`, { method: "DELETE" });
export const suggestTimes = (id: string) => call<Proposal>(`/${id}/suggest`, { method: "POST" });
export const acceptTimes = (id: string, sessions: Placement[]) => call<PlanStep[]>(`/${id}/times`, { method: "PUT", body: body({ sessions }) });
export const setStepTime = (id: string, stepId: string, startIso: string, replaceConflicts: boolean) =>
  call<ChosenTime>(`/${id}/activities/${stepId}/time`, { method: "PUT", body: body({ start_at: startIso, replace_conflicts: replaceConflicts }) });
export const clearStepTime = (id: string, stepId: string) => call<{ ok: true }>(`/${id}/activities/${stepId}/time`, { method: "DELETE" });
export const savePreferences = (prefs: Partial<PlanPreferences>) => call<PlanPreferences>("/preferences", { method: "PUT", body: body(prefs) });

/** 45 -> "45 min", 60 -> "1 h", 90 -> "1 h 30 min". */
export function formatMinutes(total: number): string {
  const minutes = Math.max(0, Math.round(total));
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** "Wed, Nov 18 · 9:00 – 10:00 AM" (the meridiem is shown once when both ends share it). */
export function formatSession(startIso: string, endIso: string): string {
  const start = new Date(startIso), end = new Date(endIso);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "Time unavailable";
  const day = start.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const clock = (value: Date) => value.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const [from, to] = [clock(start), clock(end)];
  const shared = from.slice(-2) === to.slice(-2) && /[AP]M$/i.test(from);
  return `${day} · ${shared ? from.slice(0, -3) : from} – ${to}`;
}

/** The plan's headline numbers in one line. */
export function planSummary(plan: Pick<StudyPlan, "activities" | "needed_minutes" | "scheduled_minutes">): string {
  const steps = plan.activities.length;
  if (!steps) return "No steps yet";
  const open = plan.activities.filter((step) => !step.done).length;
  const parts = [`${steps} step${steps === 1 ? "" : "s"}`];
  if (open !== steps) parts.push(`${steps - open} done`);
  if (open) parts.push(`${formatMinutes(plan.needed_minutes)} left`, `${plan.activities.filter((s) => !s.done && s.start_at).length} of ${open} scheduled`);
  return parts.join(" · ");
}

/** Local `YYYY-MM-DD` and `HH:MM` to an ISO instant, or null when either is missing or invalid. */
export function localToIso(date: string, time: string): string | null {
  if (!date || !time) return null;
  const moment = new Date(`${date}T${time}`);
  return Number.isNaN(moment.getTime()) ? null : moment.toISOString();
}

const pad = (value: number) => String(value).padStart(2, "0");
const dateText = (moment: Date) => `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}`;
const timeText = (moment: Date) => `${pad(moment.getHours())}:${pad(moment.getMinutes())}`;

/** Where the time picker starts: the next whole hour today, or 9:00 tomorrow when that would be late at night or very early. */
export function defaultPickerTime(from: Date = new Date()): { date: string; time: string } {
  const next = new Date(from);
  next.setMinutes(0, 0, 0);
  next.setHours(next.getHours() + 1);
  if (next.getHours() >= 21 || next.getHours() < 7) {
    if (next.getHours() >= 21) next.setDate(next.getDate() + 1);
    next.setHours(9, 0, 0, 0);
  }
  return { date: dateText(next), time: timeText(next) };
}

/** The picker values for an existing session, in the local time zone. */
export function pickerTimeFor(iso: string): { date: string; time: string } {
  const moment = new Date(iso);
  return { date: dateText(moment), time: timeText(moment) };
}

/** "4:00 PM" for a start plus a length, or "" when the start is not valid. */
export function endTimeText(date: string, time: string, minutes: number): string {
  const iso = localToIso(date, time);
  if (!iso) return "";
  return new Date(new Date(iso).getTime() + minutes * 60000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** One sentence naming what was replaced or overlapped after a time was saved. */
export function savedTimeMessage(result: Pick<ChosenTime, "replaced" | "overlaps">): string {
  let text = "Time saved. It now appears on your Class Schedule.";
  if (result.replaced.length) text += ` It replaced ${result.replaced.map((item) => item.title).join(" and ")}, which is now not scheduled.`;
  if (result.overlaps.length) text += ` It overlaps ${result.overlaps.map((item) => item.title).join(" and ")}, which stays where it is.`;
  return text;
}
