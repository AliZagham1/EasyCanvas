export type Course = {
  id: number;
  name: string;
  course_code: string;
  enrollment_term_id?: number;
};

export type Assignment = {
  id: number;
  name: string;
  due_at: string;
  points_possible: number;
  submitted_at?: string | null;
  status: string;
  course_id: number;
  course_code: string;
};

export async function getCourses(signal?: AbortSignal): Promise<Course[]> {
  const response = await fetch("http://127.0.0.1:8000/api/courses", { signal });
  if (!response.ok) {
    throw new Error(`Failed to fetch courses: HTTP ${response.status}`);
  }
  const courses: Course[] = await response.json();
  if (!Array.isArray(courses)) throw new Error("Invalid course list response");
  return courses;
}

/** Keep successful course results and report failed IDs instead of losing the list. */
export async function getUpcomingAssignments(courses: Course[], signal?: AbortSignal) {
  const results = await Promise.allSettled(
    courses.map(async (course) => {
      const response = await fetch(
        `http://127.0.0.1:8000/api/courses/${course.id}/upcoming`,
        { signal }
      );
      if (!response.ok) {
        throw new Error(`Failed to fetch course ${course.id} assignments: HTTP ${response.status}`);
      }
      const data: Omit<Assignment, "course_id" | "course_code">[] = await response.json();
      return data.map((assignment) => ({
        ...assignment,
        course_id: course.id,
        course_code: course.course_code,
      }));
    })
  );

  const assignments: Assignment[] = [];
  const failedCourseIds: number[] = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      assignments.push(...result.value);
    } else {
      failedCourseIds.push(courses[index].id);
      if (!signal?.aborted) {
        console.error(`Unable to load assignments for course ${courses[index].id}:`, result.reason);
      }
    }
  });
  assignments.sort((a, b) => new Date(a.due_at).getTime() - new Date(b.due_at).getTime());
  return { assignments, failedCourseIds };
}

/** Use the same local date, time, and time-zone display on both coursework pages. */
export function formatDueDate(dueAt: string | null | undefined) {
  if (!dueAt || Number.isNaN(new Date(dueAt).getTime())) return "Due date unavailable";
  return new Date(dueAt).toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
}

/** Course identity, rather than list position, selects an existing palette accent. */
export function getCourseAccent(courseId: number) {
  const accents = ["course-accent-violet", "course-accent-blue", "course-accent-green", "course-accent-amber"];
  return accents[Math.abs(courseId) % accents.length];
}
