"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { formatCourseCode } from "@/lib/course-label";
import { getCourseAccent } from "@/lib/coursework";
import { NotFoundError, formatPosted, formatSynced, getCourseDetails, materialsSummary, plural } from "@/lib/courses";
import type { CourseDetails } from "@/lib/courses";
import { buildPriorityView, getPriorities } from "@/lib/priorities";
import type { PriorityItem, RankedItem } from "@/lib/priorities";
import { compactSearchStatus, getSearchCourses } from "@/lib/search-index";
import type { CourseIndex } from "@/lib/search-index";
import { KIND_LABEL, courseTitle, formatDays, formatRange } from "@/lib/schedule";
import NextClassCard from "../../components/next-class-card";
import PriorityCard from "../../components/priority-card";

const ANNOUNCEMENT_PREVIEW = 5;
const OVERDUE_PREVIEW = 5;

export default function CoursePage() {
  return (
    <Suspense fallback={<div className="page-content"><p className="list-message" role="status">Loading course...</p></div>}>
      <CourseView />
    </Suspense>
  );
}

function CourseView() {
  const params = useParams<{ courseId: string }>();
  const courseId = Number(params.courseId);
  const validId = Number.isInteger(courseId) && courseId > 0;
  const [details, setDetails] = useState<CourseDetails | null>(null);
  const [assignments, setAssignments] = useState<PriorityItem[] | null>(null);
  const [assignmentsFailed, setAssignmentsFailed] = useState(false);
  const [readiness, setReadiness] = useState<CourseIndex | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState(false);
  const [now, setNow] = useState(0);
  const [showClosed, setShowClosed] = useState(false);
  const [showAllOverdue, setShowAllOverdue] = useState(false);
  const [showAllNews, setShowAllNews] = useState(false);

  useEffect(() => {
    if (!validId) return;
    const controller = new AbortController();
    async function load() {
      const [detail, priorities, search] = await Promise.allSettled([
        getCourseDetails(courseId, controller.signal), getPriorities({ signal: controller.signal }), getSearchCourses(controller.signal),
      ]);
      if (controller.signal.aborted) return;
      if (detail.status === "fulfilled") {
        setDetails(detail.value);
        setNow(Date.now());
      } else if (detail.reason instanceof NotFoundError) {
        setNotFound(true);
      } else {
        console.error("Failed to load the course:", detail.reason);
        setError(true);
      }
      if (priorities.status === "fulfilled") {
        setAssignments(priorities.value.assignments);
        setAssignmentsFailed(priorities.value.failed_course_ids.includes(courseId));
      } else {
        setAssignmentsFailed(true);
      }
      if (search.status === "fulfilled") setReadiness(search.value.courses.find((entry) => entry.course_id === courseId) ?? null);
      setLoading(false);
    }
    void load();
    return () => controller.abort();
  }, [courseId, validId]);

  const view = useMemo(
    () => buildPriorityView(assignments ?? [], now || 1, { courseId, level: "all", showCompleted: showClosed }),
    [assignments, courseId, now, showClosed],
  );
  const hiddenCount = view.counts.completed;

  if (validId && loading) return <div className="page-content"><p className="list-message" role="status">Loading course...</p></div>;
  if (!validId || notFound) {
    return <div className="page-content">
      <p className="list-message" role="alert">This course was not found in your saved courses. <Link href="/courses" className="text-link">Back to all courses</Link></p>
    </div>;
  }
  if (error || !details) {
    return <div className="page-content">
      <p className="list-message" role="alert">Unable to load this course. Check that the backend is running, then reload. <Link href="/courses" className="text-link">Back to all courses</Link></p>
    </div>;
  }

  const code = formatCourseCode(details.course_code, details.name || "Course");
  const overdueShown = showAllOverdue ? view.overdue : view.overdue.slice(0, OVERDUE_PREVIEW);
  const stats = [
    { label: "Overdue", value: view.counts.overdue }, { label: "Due within 24 hours", value: view.counts.within24h },
    { label: "Due in 1–3 days", value: view.counts.within72h }, { label: "Completed", value: view.counts.completed },
  ];
  const groups: { key: string; title: string; items: RankedItem[]; total: number }[] = [
    { key: "overdue", title: "Overdue", items: overdueShown, total: view.overdue.length },
    { key: "upcoming", title: "Upcoming", items: view.upcoming, total: view.upcoming.length },
    { key: "unscheduled", title: "No due date", items: view.unscheduled, total: view.unscheduled.length },
    { key: "notActionable", title: "Closed or not open", items: view.notActionable, total: view.notActionable.length },
    { key: "completed", title: "Completed", items: view.completed, total: view.completed.length },
  ];
  const nothingToShow = groups.every((group) => group.total === 0);
  const news = showAllNews ? details.announcements : details.announcements.slice(0, ANNOUNCEMENT_PREVIEW);
  const searchLabel = readiness ? (compactSearchStatus(readiness) === "Ready" ? "Study Assistant ready" : compactSearchStatus(readiness)) : null;

  return (
    <div className={`page-content course-page ${getCourseAccent(details.course_id)}`}>
      <p className="course-back"><Link href="/courses" className="primary-button btn-sm button-link">← All courses</Link></p>
      <header className="page-header">
        <div>
          <h1 className="page-title">{code}</h1>
          <p className="page-description">{courseTitle(details.name)}</p>
          <p className="course-meta">Last synced {formatSynced(details.synced_at)}{searchLabel ? ` · ${searchLabel}` : ""}</p>
        </div>
        <div className="header-actions">
          <Link href={`/study-assistant?course=${details.course_id}`} className="primary-button btn-primary button-link">Ask the Study Assistant</Link>
          {details.canvas_url && <a href={details.canvas_url} target="_blank" rel="noopener noreferrer" className="primary-button button-link">Open in Canvas<span className="sr-only"> (opens in a new tab)</span></a>}
          {details.syllabus_url && <a href={details.syllabus_url} target="_blank" rel="noopener noreferrer" className="primary-button button-link">Syllabus<span className="sr-only"> (opens in Canvas)</span></a>}
        </div>
      </header>

      <NextClassCard courses={[{ ...details, suggestions: [], hints: [], dismissed_count: 0 }]} />

      <dl className="priority-summary" aria-label="Course workload">
        {stats.map(({ label, value }) => (
          <div className="priority-stat" key={label}>
            <dt className="summary-label">{label}</dt>
            <dd className={`summary-value${assignmentsFailed ? " summary-state" : ""}`}>{assignmentsFailed || assignments === null ? "Unavailable" : value}</dd>
          </div>
        ))}
      </dl>

      <section className="work-section" aria-labelledby="course-assignments">
        <div className="work-section-head">
          <h2 id="course-assignments" className="section-title">Assignments</h2>
          {!assignmentsFailed && hiddenCount > 0 && (
            <label className="priority-check"><input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />Show completed and closed ({hiddenCount + view.notActionable.length})</label>
          )}
        </div>
        {assignmentsFailed ? (
          <p className="list-message" role="alert">Assignment status is unavailable right now, so nothing is shown here. Reload to try again.</p>
        ) : nothingToShow ? (
          <p className="list-message">{hiddenCount > 0 ? "Everything is complete. Nothing needs attention right now." : "No assignments in this course."}</p>
        ) : groups.filter((group) => group.total > 0).map((group) => (
          <div key={group.key} className="course-group">
            <h3 className="course-group-title">{group.title} <span className="priority-count">{group.total}</span></h3>
            <ul className="priority-list">{group.items.map((item) => <PriorityCard key={item.id} item={item} showCourse={false} />)}</ul>
            {group.key === "overdue" && group.total > OVERDUE_PREVIEW && (
              <button className="priority-more" onClick={() => setShowAllOverdue(!showAllOverdue)} aria-expanded={showAllOverdue}>
                {showAllOverdue ? "Show fewer" : `Show all ${group.total} overdue`}
              </button>
            )}
          </div>
        ))}
      </section>

      <section className="work-section" aria-labelledby="course-times">
        <div className="work-section-head">
          <h2 id="course-times" className="section-title">Class times</h2>
          <Link href="/class-schedule" className="primary-button btn-sm button-link">Manage in Class Schedule</Link>
        </div>
        {details.meetings.length === 0
          ? <p className="list-message">No class times yet. Add them in <Link href="/class-schedule" className="text-link">Class Schedule</Link>; your syllabus may already have suggestions.</p>
          : <ul className="meeting-list">{details.meetings.map((meeting) => (
              <li key={meeting.meeting_id}>
                <div><strong>{formatDays(meeting.days)}</strong> · {formatRange(meeting.start_time, meeting.end_time)}{meeting.location ? ` · ${meeting.location}` : ""}
                  <span className="meeting-meta">{KIND_LABEL[meeting.kind]}</span></div>
              </li>))}</ul>}
      </section>

      <section className="work-section" aria-labelledby="course-news">
        <div className="work-section-head">
          <h2 id="course-news" className="section-title">Announcements <span className="priority-count">{details.counts.announcements}</span></h2>
          <span className="panel-note">{details.counts.announcements > details.announcements.length ? `Latest ${details.announcements.length} of ${details.counts.announcements}` : "Newest first"}</span>
        </div>
        {details.announcements.length === 0 ? <p className="list-message">No announcements saved for this course.</p> : (
          <ul className="announcement-list">
            {news.map((item, index) => (
              <li key={`${item.url}-${index}`}>
                <div className="announcement-head">
                  {item.url ? <a href={item.url} target="_blank" rel="noopener noreferrer" className="priority-title">{item.title}<span className="sr-only"> (opens in Canvas)</span></a>
                    : <span className="priority-title">{item.title}</span>}
                  <time dateTime={item.posted_at ?? undefined}>{formatPosted(item.posted_at)}</time>
                </div>
                {item.excerpt && <p>{item.excerpt}</p>}
              </li>
            ))}
          </ul>
        )}
        {details.announcements.length > ANNOUNCEMENT_PREVIEW && (
          <button className="priority-more" onClick={() => setShowAllNews(!showAllNews)} aria-expanded={showAllNews}>
            {showAllNews ? "Show fewer" : `Show all ${details.announcements.length}`}
          </button>
        )}
      </section>

      <section className="work-section" aria-labelledby="course-materials">
        <div className="work-section-head">
          <h2 id="course-materials" className="section-title">Materials</h2>
          <span className="panel-note">{materialsSummary(details.counts)}</span>
        </div>
        <p className="assistant-hint course-materials-note">Files and pages saved from this course&apos;s modules. Open any item in Canvas.</p>
        {details.modules.length === 0 ? <p className="list-message">No modules saved for this course.</p> : (
          <div className="module-list">
            {details.modules.map((module, moduleIndex) => (
              <details key={`${module.module_id ?? "module"}-${moduleIndex}`} className="module">
                <summary><span>{module.name}</span><span className="module-count">{plural(module.items.length, "item")}</span></summary>
                {module.items.length === 0 ? <p className="assistant-hint">No files or pages saved for this module.</p> : (
                  <ul>{module.items.map((item, index) => (
                    <li key={`${item.title}-${index}`}>
                      <span className="module-type">{item.type}</span>
                      {item.url ? <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}<span className="sr-only"> (opens in Canvas)</span></a> : <span>{item.title}</span>}
                    </li>))}
                  </ul>
                )}
              </details>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
