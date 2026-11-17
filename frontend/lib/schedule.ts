// Class schedule: types, API client, and the pure helpers behind the weekly grid and "next class".
// Only confirmed meetings are ever used for planning; suggestions are shown for review and never trusted.

export type DayCode = "MON" | "TUE" | "WED" | "THU" | "FRI" | "SAT" | "SUN";
export type MeetingKind = "lecture" | "lab" | "discussion" | "other";

export const DAYS: DayCode[] = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
export const DAY_SHORT: Record<DayCode, string> = { MON: "Mon", TUE: "Tue", WED: "Wed", THU: "Thu", FRI: "Fri", SAT: "Sat", SUN: "Sun" };
export const DAY_LONG: Record<DayCode, string> = { MON: "Monday", TUE: "Tuesday", WED: "Wednesday", THU: "Thursday", FRI: "Friday", SAT: "Saturday", SUN: "Sunday" };
export const KIND_LABEL: Record<MeetingKind, string> = { lecture: "Lecture", lab: "Lab", discussion: "Discussion", other: "Other" };

export type Meeting = {
  meeting_id: string; course_id: number; kind: MeetingKind; days: DayCode[]; start_time: string; end_time: string;
  location: string | null; source: "manual" | "syllabus"; source_text: string | null; fingerprint: string | null;
};
export type Suggestion = {
  fingerprint: string; days: DayCode[]; start_time: string; end_time: string; location: string | null;
  kind: MeetingKind; source_text: string; confidence: "high" | "medium";
};
export type Hint = { days: DayCode[]; source_text: string };
export type ScheduleCourse = {
  course_id: number; course_code: string | null; name: string | null; time_zone: string | null;
  meetings: Meeting[]; suggestions: Suggestion[]; hints: Hint[]; dismissed_count: number;
};
export type ScheduleData = { courses: ScheduleCourse[] };
export type MeetingInput = { days: DayCode[]; start_time: string; end_time: string; kind?: MeetingKind; location?: string | null };

// ------------------------------------------------------------------ formatting
export const toMinutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));

/** "15:30" -> "3:30 PM" */
export function formatTime(time: string): string {
  const total = toMinutes(time);
  const hour = Math.floor(total / 60), minute = total % 60;
  const suffix = hour >= 12 ? "PM" : "AM";
  return `${hour % 12 || 12}:${String(minute).padStart(2, "0")} ${suffix}`;
}

export const formatRange = (start: string, end: string) => `${formatTime(start)} – ${formatTime(end)}`;

/** ["TUE","THU"] -> "Tue, Thu"; a full Mon-Fri run becomes "Mon–Fri". */
export function formatDays(days: DayCode[]): string {
  const sorted = DAYS.filter((day) => days.includes(day));
  const indexes = sorted.map((day) => DAYS.indexOf(day));
  const consecutive = sorted.length >= 3 && indexes.every((value, i) => i === 0 || value === indexes[i - 1] + 1);
  return consecutive ? `${DAY_SHORT[sorted[0]]}–${DAY_SHORT[sorted[sorted.length - 1]]}` : sorted.map((day) => DAY_SHORT[day]).join(", ");
}

// ------------------------------------------------------------------ week layout
export type Block = { meeting: Meeting; day: DayCode; startMin: number; endMin: number; column: number; columns: number };
export type WeekLayout = { days: DayCode[]; startHour: number; endHour: number; blocks: Block[] };

/**
 * Positions confirmed meetings on a Mon-Sun grid. Mon-Fri always show; Sat/Sun only when used. The hour range
 * defaults to 8-18 and grows to include every meeting. Overlapping meetings share a day column side by side.
 */
export function layoutWeek(meetings: Meeting[]): WeekLayout {
  const used = new Set(meetings.flatMap((meeting) => meeting.days));
  const days = DAYS.filter((day, index) => index < 5 || used.has(day));
  let startHour = 8, endHour = 18;
  for (const meeting of meetings) {
    startHour = Math.min(startHour, Math.floor(toMinutes(meeting.start_time) / 60));
    endHour = Math.max(endHour, Math.ceil(toMinutes(meeting.end_time) / 60));
  }
  const blocks: Block[] = [];
  for (const day of days) {
    const entries = meetings.filter((meeting) => meeting.days.includes(day))
      .map((meeting) => ({ meeting, day, startMin: toMinutes(meeting.start_time), endMin: toMinutes(meeting.end_time), column: 0, columns: 1 }))
      .sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin || a.meeting.meeting_id.localeCompare(b.meeting.meeting_id));
    let cluster: Block[] = [];
    let clusterEnd = -1;
    const closeCluster = () => {
      const width = cluster.reduce((max, block) => Math.max(max, block.column + 1), 1);
      cluster.forEach((block) => { block.columns = width; });
      blocks.push(...cluster);
      cluster = [];
      clusterEnd = -1;
    };
    for (const entry of entries) {
      if (cluster.length && entry.startMin >= clusterEnd) closeCluster();   // no overlap with the running group
      const taken = new Set(cluster.filter((block) => block.endMin > entry.startMin).map((block) => block.column));
      let column = 0;
      while (taken.has(column)) column += 1;
      entry.column = column;
      cluster.push(entry);
      clusterEnd = Math.max(clusterEnd, entry.endMin);
    }
    closeCluster();
  }
  return { days, startHour, endHour, blocks };
}

// ------------------------------------------------------------------ next class
export type NextClass = { meeting: Meeting; course: ScheduleCourse; inProgress: boolean; minutes: number; day: DayCode; daysAhead: number };

const WEEKDAY: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
const clockCache = new Map<string, Intl.DateTimeFormat>();

export function resolveZone(zone: string | null | undefined): string {
  const candidate = zone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: candidate });
    return candidate;
  } catch {
    return "UTC";
  }
}

/** Weekday index (Mon=0) and minutes since midnight at `nowMs` in `zone`. */
export function localClock(nowMs: number, zone: string): { day: number; minutes: number } {
  let formatter = clockCache.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", { timeZone: zone, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    clockCache.set(zone, formatter);
  }
  const parts = Object.fromEntries(formatter.formatToParts(nowMs).map((part) => [part.type, part.value]));
  return { day: WEEKDAY[parts.weekday], minutes: (Number(parts.hour) % 24) * 60 + Number(parts.minute) };
}

/**
 * The meeting that is in progress or starts soonest, measured on each course's own campus clock.
 * Returns null when there are no confirmed meetings.
 */
export function nextClass(courses: ScheduleCourse[], nowMs: number): NextClass | null {
  let best: NextClass | null = null;
  for (const course of courses) {
    const { day: today, minutes: now } = localClock(nowMs, resolveZone(course.time_zone));
    for (const meeting of course.meetings) {
      const start = toMinutes(meeting.start_time), end = toMinutes(meeting.end_time);
      for (const code of meeting.days) {
        const dayIndex = DAYS.indexOf(code);
        const sameDay = dayIndex === today;
        let daysAhead = (dayIndex - today + 7) % 7;
        let ahead = daysAhead * 1440 + start - now;
        const inProgress = sameDay && start <= now && now < end;
        if (!inProgress && ahead <= 0 && !(sameDay && now < start)) { daysAhead += 7; ahead += 7 * 1440; }   // already over today: next week
        const candidate: NextClass = { meeting, course, inProgress, minutes: inProgress ? end - now : ahead, day: code, daysAhead };
        const rank = (value: NextClass) => (value.inProgress ? -1 : value.minutes);
        if (!best || rank(candidate) < rank(best) || (rank(candidate) === rank(best) && meeting.meeting_id < best.meeting.meeting_id)) best = candidate;
      }
    }
  }
  return best;
}

export function describeNext(next: NextClass): string {
  const minutes = next.minutes;
  if (next.inProgress) return minutes < 60 ? `In progress, ends in ${minutes} min` : "In progress";
  const soon = next.daysAhead === 0 || minutes < 180;          // today, or starting within a few hours (even after midnight)
  if (soon) {
    if (minutes < 60) return `Starts in ${Math.max(1, minutes)} min`;
    const hours = Math.floor(minutes / 60), rest = minutes % 60;
    return rest ? `Starts in ${hours} h ${rest} min` : `Starts in ${hours} h`;
  }
  return next.daysAhead === 1 ? "Tomorrow" : `On ${DAY_LONG[next.day]}`;
}

export const courseLabel = (course: Pick<ScheduleCourse, "course_code" | "name">) => {
  const match = course.course_code?.match(/(?:^|[^a-z0-9])([a-z]{2,10})[\s_-]*(\d{3,5}[a-z]?)(?=$|[^a-z0-9])/i);
  return match ? `${match[1].toUpperCase()} ${match[2].toUpperCase()}` : course.course_code || course.name || "Course";
};

/** Canvas names look like "2268-CSE-3380-003-LINEAR ALGEBRA FOR CSE"; show only the title. */
export function courseTitle(name: string | null | undefined): string {
  const cleaned = (name ?? "").replace(/^\d{3,4}-[A-Za-z]{2,6}-\d{3,5}[A-Za-z]?-\d{2,3}[A-Za-z]?-/, "").trim();
  return cleaned || (name ?? "");
}

// ------------------------------------------------------------------ API
const base = "http://127.0.0.1:8000/api/schedule";

async function call<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await fetch(base + path, { cache: "no-store", signal, ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined });
  if (!response.ok) {
    let detail = "";
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
      else if (Array.isArray(data?.detail)) detail = "Check the values you entered and try again.";
    } catch { /* keep the generic message */ }
    throw new Error(detail || "The class schedule request failed. Check the backend and retry.");
  }
  return response.json();
}

export function getSchedule(signal?: AbortSignal): Promise<ScheduleData> {
  return call<ScheduleData>("", undefined, signal).then((data) => {
    if (!data || !Array.isArray(data.courses)) throw new Error("Invalid schedule response");
    return data;
  });
}
export const createMeeting = (courseId: number, input: MeetingInput, origin?: { fingerprint: string; source_text: string }) =>
  call<Meeting>("/meetings", { method: "POST", body: JSON.stringify({
    course_id: courseId, ...input, ...(origin ? { source: "syllabus", source_text: origin.source_text, fingerprint: origin.fingerprint } : {}) }) });
export const updateMeeting = (meetingId: string, input: Partial<MeetingInput>) =>
  call<Meeting>(`/meetings/${meetingId}`, { method: "PUT", body: JSON.stringify(input) });
export const deleteMeeting = (meetingId: string) => call<{ ok: boolean }>(`/meetings/${meetingId}`, { method: "DELETE" });
export const dismissSuggestion = (courseId: number, fingerprint: string) =>
  call<{ ok: boolean }>("/suggestions/dismiss", { method: "POST", body: JSON.stringify({ course_id: courseId, fingerprint }) });
export const restoreSuggestions = (courseId: number) => call<{ ok: boolean }>(`/courses/${courseId}/dismissed`, { method: "DELETE" });
