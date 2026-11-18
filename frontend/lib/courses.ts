// Course pages: types, API client, and the small pure helpers behind the cards and the course page.
// Assignment status always comes from the Priorities logic; this file adds no second classification.

import { buildPriorityView } from "./priorities";
import type { PriorityItem } from "./priorities";
import type { Meeting } from "./schedule";

export type CourseCounts = { assignments: number; modules: number; files: number; pages: number; quizzes: number; announcements: number };
export type CatalogCourse = {
  course_id: number; course_code: string | null; name: string | null; time_zone: string | null;
  canvas_url: string | null; synced_at: string | null; counts: CourseCounts; meetings: Meeting[];
  latest_announcement: { title: string; posted_at: string | null } | null;
};
export type Announcement = { title: string; url: string | null; posted_at: string | null; excerpt: string };
export type ModuleItem = { title: string; type: string; url: string | null };
export type CourseModule = { module_id: number | string | null; name: string; items: ModuleItem[] };
export type CourseDetails = Omit<CatalogCourse, "latest_announcement"> & {
  syllabus_url: string | null; announcements: Announcement[]; modules: CourseModule[];
};

const base = "http://127.0.0.1:8000/api";

export class NotFoundError extends Error {}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(base + path, { cache: "no-store", signal });
  if (response.status === 404) throw new NotFoundError("Course not found");
  if (!response.ok) throw new Error(`Request failed: HTTP ${response.status}`);
  return response.json();
}

export async function getCatalog(signal?: AbortSignal): Promise<CatalogCourse[]> {
  const data = await get<{ courses: CatalogCourse[] }>("/course-catalog", signal);
  if (!data || !Array.isArray(data.courses)) throw new Error("Invalid course catalog response");
  return data.courses;
}

export async function getCourseDetails(courseId: number, signal?: AbortSignal): Promise<CourseDetails> {
  const data = await get<CourseDetails>(`/courses/${courseId}/details`, signal);
  if (!data || typeof data.course_id !== "number" || !Array.isArray(data.modules) || !Array.isArray(data.announcements)) {
    throw new Error("Invalid course details response");
  }
  return data;
}

// ------------------------------------------------------------------ workload (from the Priorities rules)
export type Workload = { overdue: number; within24h: number; within72h: number; upcoming: number; unscheduled: number; completed: number };

/** Counts for one course using the same classification as the Priorities page. */
export function courseWorkload(items: PriorityItem[], courseId: number, nowMs: number): Workload {
  const view = buildPriorityView(items, nowMs, { courseId, level: "all", showCompleted: true });
  return { ...view.counts, upcoming: view.upcoming.length, unscheduled: view.unscheduled.length };
}

export type WorkloadTone = "alert" | "soon" | "calm";

/** One honest sentence about what needs attention, and how loudly to show it. */
export function describeWorkload(workload: Workload): { text: string; tone: WorkloadTone } {
  const plural = (n: number, word: string) => `${n} ${word}`;
  if (workload.overdue > 0) {
    const soon = workload.within24h + workload.within72h;
    return { text: soon > 0 ? `${plural(workload.overdue, "overdue")} · ${plural(soon, "due in the next 3 days")}` : plural(workload.overdue, "overdue"), tone: "alert" };
  }
  if (workload.within24h > 0) return { text: `${plural(workload.within24h, "due within 24 hours")}`, tone: "soon" };
  if (workload.within72h > 0) return { text: `${plural(workload.within72h, "due in the next 3 days")}`, tone: "soon" };
  if (workload.upcoming > 0) return { text: `${plural(workload.upcoming, "upcoming")}, none due within 3 days`, tone: "calm" };
  return { text: "Nothing due", tone: "calm" };
}

export function formatPosted(value: string | null | undefined): string {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function formatSynced(value: string | null | undefined): string {
  if (!value || Number.isNaN(Date.parse(value))) return "Not synced yet";
  return new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;

export function materialsSummary(counts: CourseCounts): string {
  return [plural(counts.modules, "module"), plural(counts.files, "file"), plural(counts.announcements, "announcement")].join(" · ");
}
