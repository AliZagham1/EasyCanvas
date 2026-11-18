"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { formatCourseCode } from "@/lib/course-label";
import { getCourseAccent } from "@/lib/coursework";
import { courseWorkload, describeWorkload, getCatalog, materialsSummary } from "@/lib/courses";
import type { CatalogCourse } from "@/lib/courses";
import { getPriorities } from "@/lib/priorities";
import type { PriorityItem } from "@/lib/priorities";
import { compactSearchStatus, getSearchCourses } from "@/lib/search-index";
import type { CourseIndex } from "@/lib/search-index";
import { DAY_SHORT, courseTitle, describeNext, formatRange, nextClass } from "@/lib/schedule";

export default function CoursesPage() {
  const [courses, setCourses] = useState<CatalogCourse[] | null>(null);
  const [assignments, setAssignments] = useState<PriorityItem[] | null>(null);
  const [failedIds, setFailedIds] = useState<number[]>([]);
  const [readiness, setReadiness] = useState<CourseIndex[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [now, setNow] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      // The course list is essential; assignment status and search readiness are nice-to-have and fail independently.
      const [catalog, priorities, search] = await Promise.allSettled([
        getCatalog(controller.signal), getPriorities({ signal: controller.signal }), getSearchCourses(controller.signal),
      ]);
      if (controller.signal.aborted) return;
      if (catalog.status === "fulfilled") {
        setCourses(catalog.value);
        setNow(Date.now());
      } else {
        console.error("Failed to load the course catalog:", catalog.reason);
        setError(true);
      }
      if (priorities.status === "fulfilled") { setAssignments(priorities.value.assignments); setFailedIds(priorities.value.failed_course_ids); }
      if (search.status === "fulfilled") setReadiness(search.value.courses);
      setLoading(false);
    }
    void load();
    return () => controller.abort();
  }, []);

  const sorted = [...(courses ?? [])].sort((a, b) => formatCourseCode(a.course_code).localeCompare(formatCourseCode(b.course_code)));

  return (
    <div className="page-content">
      <header className="page-header">
        <div>
          <h1 className="page-title">Courses</h1>
          <p className="page-description">Everything for each course in one place: what is due, your class times, announcements and materials.</p>
        </div>
      </header>

      {loading && <p className="list-message" role="status">Loading your courses...</p>}
      {error && !loading && <p className="list-message" role="alert">Unable to load your courses. Check that the backend is running, then reload.</p>}
      {courses && courses.length === 0 && (
        <p className="list-message" role="status">No courses are saved yet. Use Sync Canvas on the Dashboard to load them.</p>
      )}

      {sorted.length > 0 && (
        <ul className="course-grid" aria-busy={loading}>
          {sorted.map((course) => {
            const status = readiness.find((entry) => entry.course_id === course.course_id);
            const workloadKnown = assignments !== null && !failedIds.includes(course.course_id);
            const workload = workloadKnown ? describeWorkload(courseWorkload(assignments, course.course_id, now)) : null;
            const next = nextClass([{ ...course, suggestions: [], hints: [], dismissed_count: 0 }], now);
            return (
              <li key={course.course_id}>
                <Link href={`/courses/${course.course_id}`} className={`course-card ${getCourseAccent(course.course_id)}`}>
                  <span className="course-card-code">{formatCourseCode(course.course_code, course.name || "Course")}</span>
                  <span className="course-card-title">{courseTitle(course.name)}</span>
                  <span className="course-card-next">
                    {next ? <><strong>{next.inProgress ? "In class now" : "Next class"}</strong> · {DAY_SHORT[next.day]} {formatRange(next.meeting.start_time, next.meeting.end_time)}
                      {next.meeting.location ? ` · ${next.meeting.location}` : ""} <em>{describeNext(next)}</em></>
                      : <span className="course-card-muted">No class times yet</span>}
                  </span>
                  <span className={`course-card-work course-work-${workload?.tone ?? "unknown"}`}>{workload ? workload.text : "Assignment status unavailable"}</span>
                  <span className="course-card-foot">
                    <span>{materialsSummary(course.counts)}</span>
                    {status && <span className="course-card-search">{compactSearchStatus(status) === "Ready" ? "Study Assistant ready" : compactSearchStatus(status)}</span>}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
