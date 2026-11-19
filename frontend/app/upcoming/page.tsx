"use client";

import { useEffect, useMemo, useState } from "react";

import { formatDayHeading, formatDueDate, formatDueTime, getCourses, getUpcomingAssignments } from "@/lib/coursework";
import type { Course, Assignment } from "@/lib/coursework";
import Link from "next/link";
import { formatCourseCode } from "@/lib/course-label";
import { examItems, getPlans } from "@/lib/study-plans";
import type { StudyPlan } from "@/lib/study-plans";
import { describeDay, localDayNumber, resolveTimeZone } from "@/lib/priorities";

export default function UpcomingPage() {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [plans, setPlans] = useState<StudyPlan[]>([]);
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
    // Exams and other dates from Study Plans are optional: without them this is just the Canvas list.
    getPlans(controller.signal).then((payload) => { if (!controller.signal.aborted) setPlans(payload.plans); }).catch(() => {});
    return () => controller.abort();
  }, []);

  const zone = useMemo(() => resolveTimeZone(), []);
  type Row = { key: string; title: string; courseId: number; course: string; courseTitle: string; dueAt: string; points: number | null | undefined; exam: boolean };
  const rows: Row[] = useMemo(() => [
    ...assignments.map((a) => ({ key: `${a.course_id}-${a.id}`, title: a.name, courseId: a.course_id, course: formatCourseCode(a.course_code), courseTitle: a.course_code,
      dueAt: a.due_at, points: a.points_possible, exam: false })),
    ...examItems(plans, now).map((e) => ({ key: e.key, title: e.title, courseId: e.courseId, course: formatCourseCode(e.courseCode, e.courseName || "Course"),
      courseTitle: e.courseCode ?? "", dueAt: e.dueAt, points: undefined, exam: true })),
  ].sort((a, b) => Date.parse(a.dueAt) - Date.parse(b.dueAt)), [assignments, plans, now]);
  const days = useMemo(() => {
    const groups: { key: number; heading: string; relative: string; items: Row[] }[] = [];
    for (const row of rows) {
      const ms = Date.parse(row.dueAt);
      if (Number.isNaN(ms)) continue;
      const key = localDayNumber(ms, zone);
      const last = groups[groups.length - 1];
      if (last && last.key === key) last.items.push(row);
      else groups.push({ key, heading: formatDayHeading(row.dueAt, now || ms), relative: now ? describeDay(ms, now, zone) : "", items: [row] });
    }
    return groups;
  }, [rows, now, zone]);
  const assignmentsUnavailable = hasError || (courses.length > 0 && failedCourseIds.length === courses.length);
  const hasPartialCoverage = failedCourseIds.length > 0 && !assignmentsUnavailable;
  const failedCourseNames = courses.filter((course) => failedCourseIds.includes(course.id))
    .map((course) => course.course_code || course.name).join(", ");

  return (
    <div className="page-content">
      <header className="page-header">
        <div>
          <h1 className="page-title">Upcoming</h1>
          <p className="page-description">Future unsubmitted assignments from Canvas, and exams you added in Study Plans.</p>
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
            All upcoming {!loading && !assignmentsUnavailable && <span className="priority-count">{rows.length}{hasPartialCoverage ? " (partial)" : ""}</span>}
          </h2>
          <span className="panel-note">Earliest deadline first</span>
        </div>
        {loading || assignmentsUnavailable ? (
          <p className="list-message" role={loading ? "status" : "alert"}>
            {loading ? "Loading upcoming assignments..." : "Unable to load upcoming assignments. Reload this page to try again."}
          </p>
        ) : rows.length === 0 ? (
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
                {day.items.map((row) => (
                  <li key={row.key} className={`work-row${row.exam ? " is-exam" : ""}`}>
                    <div className="work-main">
                      <span className="work-title-line">
                        {row.exam ? <Link href="/study-plans" className="work-title work-title-link">{row.title}</Link> : <span className="work-title">{row.title}</span>}
                        {row.exam && <span className="due-tag">Exam</span>}
                      </span>
                      <p className="work-meta" title={row.courseTitle}>
                        {row.course}
                        {!row.exam && <><span aria-hidden="true"> · </span>{row.points == null ? "Points unavailable" : `${row.points} points`}</>}
                      </p>
                    </div>
                    <div className="work-due">
                      <time dateTime={row.dueAt} title={formatDueDate(row.dueAt)}>{formatDueTime(row.dueAt)}</time>
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
