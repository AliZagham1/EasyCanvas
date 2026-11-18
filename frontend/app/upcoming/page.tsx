"use client";

import { useEffect, useMemo, useState } from "react";

import { formatDayHeading, formatDueDate, formatDueTime, getCourses, getUpcomingAssignments } from "@/lib/coursework";
import type { Course, Assignment } from "@/lib/coursework";
import { formatCourseCode } from "@/lib/course-label";
import { describeDay, localDayNumber, resolveTimeZone } from "@/lib/priorities";

export default function UpcomingPage() {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  const [courses, setCourses] = useState<Course[]>([]);
  const [failedCourseIds, setFailedCourseIds] = useState<number[]>([]);
  const [now, setNow] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function loadAssignments() {
      try {
        const courseData = await getCourses(controller.signal);
        if (controller.signal.aborted) return;
        setCourses(courseData);
        const result = await getUpcomingAssignments(courseData, controller.signal);
        if (controller.signal.aborted) return;
        setAssignments(result.assignments);
        setNow(Date.now());
        setFailedCourseIds(result.failedCourseIds);
      } catch (error) {
        if (controller.signal.aborted) return;
        console.error("Failed to load EasyCanvas upcoming assignments:", error);
        setHasError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    loadAssignments();
    return () => controller.abort();
  }, []);

  const zone = useMemo(() => resolveTimeZone(), []);
  const days = useMemo(() => {
    const groups: { key: number; heading: string; relative: string; items: Assignment[] }[] = [];
    for (const assignment of assignments) {
      const ms = Date.parse(assignment.due_at);
      if (Number.isNaN(ms)) continue;
      const key = localDayNumber(ms, zone);
      const last = groups[groups.length - 1];
      if (last && last.key === key) last.items.push(assignment);
      else groups.push({ key, heading: formatDayHeading(assignment.due_at, now || ms), relative: now ? describeDay(ms, now, zone) : "", items: [assignment] });
    }
    return groups;
  }, [assignments, now, zone]);
  const assignmentsUnavailable = hasError || (courses.length > 0 && failedCourseIds.length === courses.length);
  const hasPartialCoverage = failedCourseIds.length > 0 && !assignmentsUnavailable;
  const failedCourseNames = courses.filter((course) => failedCourseIds.includes(course.id))
    .map((course) => course.course_code || course.name).join(", ");

  return (
    <div className="page-content">
      <header className="page-header">
        <div>
          <h1 className="page-title">Upcoming Assignments</h1>
          <p className="page-description">All future unsubmitted assignments from Canvas.</p>
        </div>
      </header>

      {hasPartialCoverage && (
        <p className="coverage-notice" role="status">
          Partial Canvas coverage: assignments could not be loaded for {failedCourseNames}.
          Counts and deadlines include loaded courses only. Reload this page to try again.
        </p>
      )}

      <section className="work-section" aria-labelledby="all-upcoming-title" aria-busy={loading}>
        <div className="work-section-head">
          <h2 id="all-upcoming-title" className="section-title">
            All upcoming {!loading && !assignmentsUnavailable && <span className="priority-count">{assignments.length}{hasPartialCoverage ? " (partial)" : ""}</span>}
          </h2>
          <span className="panel-note">Earliest deadline first</span>
        </div>
        {loading || assignmentsUnavailable ? (
          <p className="list-message" role={loading ? "status" : "alert"}>
            {loading ? "Loading upcoming assignments..." : "Unable to load upcoming assignments. Reload this page to try again."}
          </p>
        ) : assignments.length === 0 ? (
          <p className="list-message">
            {hasPartialCoverage ? "No upcoming assignments in loaded courses. Other courses are unavailable." : "No upcoming assignments found."}
          </p>
        ) : (
          days.map((day) => (
            <div key={day.key} role="group" aria-labelledby={`day-${day.key}`}>
              <h3 id={`day-${day.key}`} className="work-group-title">
                {day.heading}{day.relative && <span className="work-group-note"> · {day.relative}</span>}
              </h3>
              <ul className="work-list">
                {day.items.map((assignment) => (
                  <li key={`${assignment.course_id}-${assignment.id}`} className="work-row">
                    <div className="work-main">
                      <span className="work-title">{assignment.name}</span>
                      <p className="work-meta" title={assignment.course_code}>
                        {formatCourseCode(assignment.course_code)}
                        <span aria-hidden="true"> · </span>
                        {assignment.points_possible == null ? "Points unavailable" : `${assignment.points_possible} points`}
                      </p>
                    </div>
                    <div className="work-due">
                      <time dateTime={assignment.due_at} title={formatDueDate(assignment.due_at)}>{formatDueTime(assignment.due_at)}</time>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </section>
    </div>
  );
}
