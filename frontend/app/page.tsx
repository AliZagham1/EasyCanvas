"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { getCourses, getUpcomingAssignments, formatDueDate, formatDueShort, getCourseAccent } from "@/lib/coursework";
import type { Course, Assignment } from "@/lib/coursework";
import { formatCourseCode } from "@/lib/course-label";
import NextClassCard from "./components/next-class-card";
import { describeDay, describeDue, resolveTimeZone, urgencyBucket } from "@/lib/priorities";
import { courseTitle } from "@/lib/schedule";
import { examItems, getPlans } from "@/lib/study-plans";
import type { StudyPlan } from "@/lib/study-plans";
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
  const [plans, setPlans] = useState<StudyPlan[]>([]);
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

  // Exams and other dates entered in Study Plans. Optional: without them the list is just Canvas assignments.
  useEffect(() => {
    const controller = new AbortController();
    getPlans(controller.signal).then((payload) => { if (!controller.signal.aborted) setPlans(payload.plans); }).catch(() => {});
    return () => controller.abort();
  }, []);

  /** Assignments and exams in one list, earliest first. */
  const rows = useMemo(() => [
    ...assignments.map((a) => ({ key: `${a.course_id}-${a.id}`, title: a.name, courseId: a.course_id, course: formatCourseCode(a.course_code), courseTitle: a.course_code, dueAt: a.due_at, exam: false })),
    ...examItems(plans, now).map((e) => ({ key: e.key, title: e.title, courseId: e.courseId, course: formatCourseCode(e.courseCode, e.courseName || "Course"), courseTitle: e.courseCode ?? "", dueAt: e.dueAt, exam: true })),
  ].sort((a, b) => Date.parse(a.dueAt) - Date.parse(b.dueAt)), [assignments, plans, now]);

  function getUpcomingCount(courseId: number) {
    return rows.filter((row) => row.courseId === courseId).length;
  }

  const assignmentsUnavailable = hasError || (courses.length > 0 && failedCourseIds.length === courses.length);
  const hasPartialCoverage = failedCourseIds.length > 0 && !assignmentsUnavailable;
  const failedCourseNames = courses.filter((course) => failedCourseIds.includes(course.id))
    .map((course) => formatCourseCode(course.course_code, course.name)).join(", ");

  const zone = useMemo(() => resolveTimeZone(), []);
  const grouped = useMemo(() => URGENCY_GROUPS.map((group) => ({
    ...group,
    items: now === 0 ? [] : rows.slice(0, PREVIEW).filter((row) => urgencyBucket(Date.parse(row.dueAt), now) === group.key),
  })).filter((group) => group.items.length > 0), [rows, now]);
  const counts = useMemo(() => ({
    soon: rows.filter((row) => urgencyBucket(Date.parse(row.dueAt), now) === "soon").length,
    next: rows.filter((row) => urgencyBucket(Date.parse(row.dueAt), now) === "next").length,
  }), [rows, now]);
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
            {isLoading ? "..." : assignmentsUnavailable ? "Unavailable" : rows.length}
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
            <h2 id="upcoming-title" className="section-title">Upcoming</h2>
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
          ) : rows.length === 0 ? (
            <p className="list-message">{hasPartialCoverage ? "No upcoming assignments in loaded courses. Other courses are unavailable." : "No upcoming assignments found."}</p>
          ) : (
            <>
              {grouped.map((group) => (
                <div key={group.key} role="group" aria-labelledby={`group-${group.key}`}>
                  <h3 id={`group-${group.key}`} className="work-group-title">{group.title}</h3>
                  <ul className="work-list">
                    {group.items.map((row) => (
                      <li key={row.key} className={`work-row${row.exam ? " is-exam" : ""}`}>
                        <div className="work-main">
                          <span className="work-title-line">
                            {row.exam ? <Link href="/study-plans" className="work-title work-title-link">{row.title}</Link> : <span className="work-title">{row.title}</span>}
                            {row.exam && <span className="due-tag">Exam</span>}
                          </span>
                          <p className="work-meta" title={row.courseTitle}>{row.course}</p>
                        </div>
                        <div className="work-due">
                          <time dateTime={row.dueAt} title={formatDueDate(row.dueAt)}>{formatDueShort(row.dueAt, now)}</time>
                          <span className={`work-relative${group.key === "soon" ? " is-soon" : ""}`}>{row.exam ? describeDay(Date.parse(row.dueAt), now, zone) : describeDue(Date.parse(row.dueAt), now, zone)}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {rows.length > PREVIEW && <p className="work-more">Showing the next {PREVIEW} of {rows.length}.</p>}
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
