"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { getCourses, getUpcomingAssignments, formatDueDate, formatDueShort, getCourseAccent } from "@/lib/coursework";
import type { Course, Assignment } from "@/lib/coursework";
import { formatCourseCode } from "@/lib/course-label";
import NextClassCard from "./components/next-class-card";
import { describeDue, resolveTimeZone, urgencyBucket } from "@/lib/priorities";
import { courseTitle } from "@/lib/schedule";
import { useCourseSync } from "@/lib/use-course-sync";

const PREVIEW = 8;
const URGENCY_GROUPS = [
  { key: "soon", title: "Due within 24 hours" },
  { key: "next", title: "Due in 1–3 days" },
  { key: "later", title: "Later" },
] as const;

export default function Home() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [coursesLoading, setCoursesLoading] = useState(true);
  const [coursesError, setCoursesError] = useState(false);
  const [failedCourseIds, setFailedCourseIds] = useState<number[]>([]);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [now, setNow] = useState(0);
  const syncCompleted = useCallback(() => {
    setCoursesLoading(true);
    setIsLoading(true);
    setCoursesError(false);
    setHasError(false);
    setFailedCourseIds([]);
    setRefreshVersion((version) => version + 1);
  }, []);
  const courseSync = useCourseSync(syncCompleted);

  useEffect(() => {
    const controller = new AbortController();
    async function loadDashboard() {
      let coursesLoaded = false;
      try {
        const courseData = await getCourses(controller.signal);
        if (controller.signal.aborted) return;
        setCourses(courseData);
        coursesLoaded = true;
        setCoursesLoading(false);

        const result = await getUpcomingAssignments(courseData, controller.signal);
        if (controller.signal.aborted) return;
        setAssignments(result.assignments);
        setNow(Date.now());
        setFailedCourseIds(result.failedCourseIds);
      } catch (error) {
        if (controller.signal.aborted) return;
        console.error("Failed to load EasyCanvas dashboard:", error);
        setCoursesError(!coursesLoaded);
        setHasError(true);
      } finally {
        if (!controller.signal.aborted) {
          setCoursesLoading(false);
          setIsLoading(false);
        }
      }
    }

    loadDashboard();
    return () => controller.abort();
  }, [refreshVersion]);

  function getUpcomingCount(courseId: number) {
    return assignments.filter((assignment) => assignment.course_id === courseId).length;
  }

  const assignmentsUnavailable = hasError || (courses.length > 0 && failedCourseIds.length === courses.length);
  const hasPartialCoverage = failedCourseIds.length > 0 && !assignmentsUnavailable;
  const failedCourseNames = courses.filter((course) => failedCourseIds.includes(course.id))
    .map((course) => formatCourseCode(course.course_code, course.name)).join(", ");

  const zone = useMemo(() => resolveTimeZone(), []);
  const grouped = useMemo(() => URGENCY_GROUPS.map((group) => ({
    ...group,
    items: now === 0 ? [] : assignments.slice(0, PREVIEW).filter((a) => urgencyBucket(Date.parse(a.due_at), now) === group.key),
  })).filter((group) => group.items.length > 0), [assignments, now]);
  const counts = useMemo(() => ({
    soon: assignments.filter((a) => urgencyBucket(Date.parse(a.due_at), now) === "soon").length,
    next: assignments.filter((a) => urgencyBucket(Date.parse(a.due_at), now) === "next").length,
  }), [assignments, now]);
  const statValue = (value: number) => isLoading || now === 0 ? "..." : assignmentsUnavailable ? "Unavailable" : value;

  return (
    <div className="page-content">
      <header className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-description">What needs your attention.</p>
        </div>
        <button
          className="primary-button"
          onClick={() => void courseSync.startSync()}
          disabled={courseSync.busy}
          aria-busy={courseSync.busy}
        >
          {courseSync.busy ? "Syncing Canvas..." : "Sync Canvas"}
        </button>
      </header>

      <dl className="stat-strip" style={{ "--stats": 3 } as React.CSSProperties} aria-label="Coursework overview" aria-busy={isLoading}>
        <div className="stat">
          <dt className="summary-label">Due within 24 hours</dt>
          <dd className={`summary-value${assignmentsUnavailable ? " summary-state" : ""}`}>{statValue(counts.soon)}</dd>
        </div>
        <div className="stat">
          <dt className="summary-label">Due in 1–3 days</dt>
          <dd className={`summary-value${assignmentsUnavailable ? " summary-state" : ""}`}>{statValue(counts.next)}</dd>
        </div>
        <div className="stat">
          <dt className="summary-label">All upcoming</dt>
          <dd className={`summary-value${assignmentsUnavailable ? " summary-state" : ""}`}>
            {isLoading ? "..." : assignmentsUnavailable ? "Unavailable" : assignments.length}
            {!isLoading && !assignmentsUnavailable && hasPartialCoverage && <span className="summary-qualifier"> (partial)</span>}
          </dd>
        </div>
      </dl>

      {hasPartialCoverage && (
        <p className="coverage-notice" role="status">
          Partial Canvas coverage: assignments could not be loaded for {failedCourseNames}.
          Counts and deadlines include loaded courses only. Reload this page to try again.
        </p>
      )}

      <div className="dashboard-grid">
        <section className="work-section dashboard-upcoming" aria-labelledby="upcoming-title" aria-busy={isLoading}>
          <div className="work-section-head">
            <h2 id="upcoming-title" className="section-title">Upcoming Assignments</h2>
            <Link href="/upcoming" className="primary-button btn-sm button-link">
              View all upcoming
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
                <path d="M5 12h14m-6-6 6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Link>
          </div>
          {isLoading ? (
            <p className="list-message" role="status">Loading upcoming assignments...</p>
          ) : assignmentsUnavailable ? (
            <p className="list-message" role="alert">Unable to load upcoming assignments. Reload this page to try again.</p>
          ) : assignments.length === 0 ? (
            <p className="list-message">{hasPartialCoverage ? "No upcoming assignments in loaded courses. Other courses are unavailable." : "No upcoming assignments found."}</p>
          ) : (
            <>
              {grouped.map((group) => (
                <div key={group.key} role="group" aria-labelledby={`group-${group.key}`}>
                  <h3 id={`group-${group.key}`} className="work-group-title">{group.title}</h3>
                  <ul className="work-list">
                    {group.items.map((assignment) => (
                      <li key={`${assignment.course_id}-${assignment.id}`} className="work-row">
                        <div className="work-main">
                          <span className="work-title">{assignment.name}</span>
                          <p className="work-meta" title={assignment.course_code}>{formatCourseCode(assignment.course_code)}</p>
                        </div>
                        <div className="work-due">
                          <time dateTime={assignment.due_at} title={formatDueDate(assignment.due_at)}>{formatDueShort(assignment.due_at, now)}</time>
                          <span className={`work-relative${group.key === "soon" ? " is-soon" : ""}`}>{describeDue(Date.parse(assignment.due_at), now, zone)}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {assignments.length > PREVIEW && <p className="work-more">Showing the next {PREVIEW} of {assignments.length}.</p>}
            </>
          )}
        </section>

        <div className="dashboard-next"><NextClassCard /></div>

        <section className="work-section dashboard-courses" aria-labelledby="courses-title" aria-busy={coursesLoading}>
          <div className="work-section-head">
            <h2 id="courses-title" className="section-title">Courses {!coursesLoading && !coursesError && <span className="priority-count">{courses.length}</span>}</h2>
          </div>
          {coursesLoading ? (
            <p className="list-message" role="status">Loading your courses...</p>
          ) : coursesError ? (
            <p className="list-message" role="alert">Unable to load courses from Canvas. Reload this page to try again.</p>
          ) : courses.length === 0 ? (
            <p className="list-message">No current courses found in Canvas.</p>
          ) : (
            <ul className="course-list">
              {courses.map((course) => {
                const courseLabel = formatCourseCode(course.course_code, course.name);
                const count = isLoading ? "Loading..." : hasError || failedCourseIds.includes(course.id) ? "Unavailable" : `${getUpcomingCount(course.id)} upcoming`;
                return (
                  <li key={course.id} className={getCourseAccent(course.id)}>
                    <Link href={`/courses/${course.id}`} className="course-row" title={course.name}>
                      <span className="course-dot" aria-hidden="true" />
                      <span className="course-row-code">{courseLabel}</span>
                      <span className="course-row-name">{courseTitle(course.name)}</span>
                      <span className="course-row-count">{count}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
