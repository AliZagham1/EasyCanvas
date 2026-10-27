export type CourseSyncStatus = {
  run_id?: string;
  status: "idle" | "queued" | "running" | "completed" | "error" | "interrupted";
  total_courses?: number | null;
  completed_courses?: number;
  failed_courses?: number;
  current_course_id?: number | null;
  coverage?: "ok" | "partial" | "unavailable" | null;
  courses: { course_id: number; course_code?: string; status: string }[];
};

export function isCourseSyncActive(status: CourseSyncStatus | null) {
  return status?.status === "queued" || status?.status === "running";
}

function validateStatus(data: CourseSyncStatus) {
  if (!data || !Array.isArray(data.courses) || !["idle", "queued", "running", "completed", "error", "interrupted"].includes(data.status)) {
    throw new Error("Invalid course sync response");
  }
  return data;
}

export async function fetchCourseSync(signal?: AbortSignal): Promise<CourseSyncStatus> {
  const response = await fetch("http://127.0.0.1:8000/api/ingestion", { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`Unable to load course sync: HTTP ${response.status}`);
  return validateStatus(await response.json());
}

export async function requestCourseSync(signal?: AbortSignal): Promise<CourseSyncStatus> {
  const response = await fetch("http://127.0.0.1:8000/api/ingestion/sync", { method: "POST", signal });
  if (!response.ok) throw new Error(`Unable to start course sync: HTTP ${response.status}`);
  return validateStatus(await response.json());
}

/** Describe actual progress without implying partial course content is complete. */
export function describeCourseSync(sync: CourseSyncStatus | null) {
  if (!sync || sync.status === "idle") return "";
  if (sync.status === "queued") return "Preparing to sync your current Canvas courses...";
  if (sync.status === "running") {
    if (sync.total_courses == null) return "Finding your current Canvas courses...";
    const index = sync.courses.findIndex((course) => course.course_id === sync.current_course_id);
    const current = sync.courses[index];
    return current
      ? `Syncing course ${index + 1} of ${sync.total_courses}: ${current.course_code || "Canvas course"}. Your dashboard remains available.`
      : "Organizing course content...";
  }
  if (sync.status === "error") return "Course content could not be synced. Use Sync Canvas to try again.";
  if (sync.status === "interrupted") return "Course sync was interrupted. Use Sync Canvas to continue.";
  if (sync.total_courses === 0) return "Canvas returned no current courses to sync.";
  const summary = `Course sync finished: ${sync.completed_courses ?? 0} of ${sync.total_courses} courses processed.`;
  return sync.coverage === "partial"
    ? `${summary} Some sources or document content could not be read.`
    : summary;
}
