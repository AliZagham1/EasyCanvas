"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { getCourses, getUpcomingAssignments, formatDueDate, getCourseAccent } from "@/lib/coursework";
import type { Course, Assignment } from "@/lib/coursework";
import { formatCourseCode } from "@/lib/course-label";
import AssignmentTable from "./components/assignment-table";
import { useCourseSync } from "@/lib/use-course-sync";

export default function Home() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [coursesLoading, setCoursesLoading] = useState(true);
  const [coursesError, setCoursesError] = useState(false);
  const [failedCourseIds, setFailedCourseIds] = useState<number[]>([]);
  const [refreshVersion, setRefreshVersion] = useState(0);
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

  const nextAssignment = assignments[0];

  return (
    <div className="page-content">
      <header className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-description">Your current academic workload at a glance.</p>
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

      <dl className="summary-grid" aria-label="Coursework overview">
        <div className="summary-card" aria-busy={coursesLoading}>
          <dt className="summary-label">Current Courses</dt>
          <dd className={`summary-value${coursesError ? " summary-state" : ""}`}>
            {coursesLoading ? "..." : coursesError ? "Unavailable" : courses.length}
          </dd>
        </div>
        <div className="summary-card" aria-busy={isLoading}>
          <dt className="summary-label">Upcoming Assignments</dt>
          <dd className={`summary-value${assignmentsUnavailable ? " summary-state" : ""}`}>
            {isLoading ? "..." : assignmentsUnavailable ? "Unavailable" : assignments.length}
            {!isLoading && !assignmentsUnavailable && hasPartialCoverage && (
              <span className="summary-qualifier"> (partial)</span>
            )}
          </dd>
        </div>
        <div className="summary-card" aria-busy={isLoading}>
          <dt className="summary-label">Next Deadline</dt>
          <dd className="deadline-summary">
            {isLoading ? "Loading..." : assignmentsUnavailable ? "Unable to load deadlines" : nextAssignment ? (
              <>
                {nextAssignment.name}{" — "}
                <time dateTime={nextAssignment.due_at}>{formatDueDate(nextAssignment.due_at)}</time>
                <span className="summary-note">{formatCourseCode(nextAssignment.course_code)}</span>
                {hasPartialCoverage && <span className="summary-note">Earliest among loaded courses.</span>}
              </>
            ) : hasPartialCoverage ? "No upcoming work in loaded courses" : "No upcoming work"}
          </dd>
        </div>
      </dl>

      {hasPartialCoverage && (
        <p className="coverage-notice" role="status">
          Partial Canvas coverage: assignments could not be loaded for {failedCourseNames}.
          Counts and deadlines include loaded courses only. Reload this page to try again.
        </p>
      )}

      <section className="coursework-panel" aria-labelledby="upcoming-title" aria-busy={isLoading}>
        <div className="panel-header">
          <h2 id="upcoming-title" className="section-title">Upcoming Assignments</h2>
          <Link href="/upcoming" className="text-link">
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
          <AssignmentTable assignments={assignments.slice(0, 5)} caption="Next five upcoming assignments, earliest deadline first" />
        )}
      </section>

      <section className="coursework-panel" aria-labelledby="courses-title" aria-busy={coursesLoading}>
        <div className="panel-header">
          <h2 id="courses-title" className="section-title">Current Courses</h2>
          <span className="panel-note">From Canvas</span>
        </div>
        {coursesLoading ? (
          <p className="list-message" role="status">Loading your courses...</p>
        ) : coursesError ? (
          <p className="list-message" role="alert">Unable to load courses from Canvas. Reload this page to try again.</p>
        ) : courses.length === 0 ? (
          <p className="list-message">No current courses found in Canvas.</p>
        ) : (
          <table className="coursework-table courses-table" role="table">
            <caption className="sr-only">Current Canvas courses and upcoming assignment counts</caption>
            <colgroup>
              <col className="course-code-column" />
              <col className="course-name-column" />
              <col className="course-upcoming-column" />
            </colgroup>
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">Course</th>
                <th scope="col" role="columnheader">Name</th>
                <th scope="col" role="columnheader">Upcoming</th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {courses.map((course) => {
                const courseLabel = formatCourseCode(course.course_code, course.name);
                return (
                  <tr key={course.id} className={getCourseAccent(course.id)} role="row">
                    <td data-label="Course" role="cell">
                      <div className="course-identity" title={course.course_code}>
                        <span className="course-dot" aria-hidden="true" />
                        <span className="course-code">{courseLabel}</span>
                      </div>
                    </td>
                    <td data-label="Name" role="cell">
                      <div>
                        {course.name}
                        {course.course_code && course.course_code !== courseLabel && course.course_code !== course.name && (
                          <span className="cell-secondary">{course.course_code}</span>
                        )}
                      </div>
                    </td>
                    <td data-label="Upcoming" role="cell">
                      <div className="course-upcoming">
                        <span>{isLoading ? "Loading..." : hasError || failedCourseIds.includes(course.id) ? "Assignments unavailable" : `${getUpcomingCount(course.id)} upcoming`}</span>
                        <span className="course-view-label" aria-disabled="true" title="Course view is not available yet">View →</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
