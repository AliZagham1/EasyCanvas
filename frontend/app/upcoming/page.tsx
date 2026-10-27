"use client";

import { useEffect, useState } from "react";

import { getCourses, getUpcomingAssignments } from "@/lib/coursework";
import type { Course, Assignment } from "@/lib/coursework";
import AssignmentTable from "../components/assignment-table";

export default function UpcomingPage() {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  const [courses, setCourses] = useState<Course[]>([]);
  const [failedCourseIds, setFailedCourseIds] = useState<number[]>([]);

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

      <dl className="upcoming-summary" aria-busy={loading}>
        <div>
          <dt className="summary-label">{hasPartialCoverage ? "Upcoming from loaded courses" : "Total Upcoming"}</dt>
          <dd className={`summary-value${assignmentsUnavailable ? " summary-state" : ""}`}>
            {loading ? "..." : assignmentsUnavailable ? "Unavailable" : assignments.length}
          </dd>
        </div>
      </dl>

      {hasPartialCoverage && (
        <p className="coverage-notice" role="status">
          Partial Canvas coverage: assignments could not be loaded for {failedCourseNames}.
          Counts and deadlines include loaded courses only. Reload this page to try again.
        </p>
      )}

      <section className="coursework-panel" aria-labelledby="all-upcoming-title" aria-busy={loading}>
        <div className="panel-header">
          <h2 id="all-upcoming-title" className="section-title">All Upcoming</h2>
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
          <AssignmentTable assignments={assignments} caption="All upcoming assignments, earliest deadline first" showPoints />
        )}
      </section>
    </div>
  );
}
