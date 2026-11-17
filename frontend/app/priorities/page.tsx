"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { formatCourseCode } from "@/lib/course-label";
import { buildPriorityView, getPriorities } from "@/lib/priorities";
import type { Filters, PrioritiesPayload, PriorityLevel, RankedItem, SortMode } from "@/lib/priorities";
import { useCourseSync } from "@/lib/use-course-sync";
import PriorityCard from "../components/priority-card";

const CLOCK_REFRESH_MS = 60_000;

export default function PrioritiesPage() {
  const [data, setData] = useState<PrioritiesPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [now, setNow] = useState(0);
  const [filters, setFilters] = useState<Filters>({ courseId: "all", level: "all", showCompleted: false });
  const [sort, setSort] = useState<SortMode>("recommended");
  const controller = useRef<AbortController | null>(null);

  // A newer request always supersedes an older one, so a slow response cannot replace fresher data.
  const fetchPriorities = useCallback(async (refresh: boolean) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    try {
      const payload = await getPriorities({ refresh, signal: current.signal });
      if (current.signal.aborted) return;
      setData(payload);
      setHasError(false);
      setNow(Date.now());
    } catch (error) {
      if (current.signal.aborted) return;
      console.error("Failed to load EasyCanvas priorities:", error);
      setHasError(true);
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }, []);
  const load = useCallback((refresh: boolean) => {
    setLoading(true);
    return fetchPriorities(refresh);
  }, [fetchPriorities]);

  useEffect(() => {
    const initial = new AbortController();
    controller.current = initial;
    async function loadInitial() {
      try {
        const payload = await getPriorities({ signal: initial.signal });
        if (initial.signal.aborted) return;
        setData(payload);
        setNow(Date.now());
      } catch (error) {
        if (initial.signal.aborted) return;
        console.error("Failed to load EasyCanvas priorities:", error);
        setHasError(true);
      } finally {
        if (!initial.signal.aborted) setLoading(false);
      }
    }
    void loadInitial();
    return () => controller.current?.abort();
  }, []);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_REFRESH_MS);
    return () => clearInterval(timer);
  }, []);

  // After a Canvas sync finishes, bypass the short backend cache so newer synced data wins.
  const syncCompleted = useCallback(() => void load(true), [load]);
  const courseSync = useCourseSync(syncCompleted);

  const courses = data?.courses ?? [];
  const failedIds = data?.failed_course_ids ?? [];
  const unavailable = hasError || (data !== null && courses.length > 0 && failedIds.length === courses.length);
  const partial = !unavailable && failedIds.length > 0;
  const failedNames = courses.filter((c) => failedIds.includes(c.id)).map((c) => formatCourseCode(c.course_code, c.name || "Unknown course")).join(", ");
  const showData = data !== null && !unavailable;
  const view = useMemo(() => buildPriorityView(data?.assignments ?? [], now, filters, sort), [data, now, filters, sort]);

  const sections: { key: string; title: string; note: string; items: RankedItem[] }[] = [
    { key: "active", title: sort === "recommended" ? "Recommended order" : "Earliest deadline first",
      note: sort === "recommended" ? "Most urgent first; heavier work first on the same day" : "Overdue work first", items: view.active },
    { key: "unscheduled", title: "No due date", note: "Not ranked by urgency", items: view.unscheduled },
    { key: "notActionable", title: "Not actionable", note: "Closed, locked, excused or not yet open", items: view.notActionable },
    { key: "completed", title: "Completed", note: "Submitted or graded", items: view.completed },
  ];
  const visibleCount = sections.reduce((total, section) => total + section.items.length, 0);

  let emptyMessage = "";
  if (showData && visibleCount === 0) {
    const noWork = data.assignments.length === 0;
    if (courses.length === 0) emptyMessage = "No current courses found.";
    else if (noWork) emptyMessage = partial ? "No assignments in loaded courses. Other courses are unavailable." : "No assignments found.";
    else if (filters.courseId !== "all" || filters.level !== "all") emptyMessage = "No assignments match these filters.";
    else if (view.counts.completed > 0) emptyMessage = "Everything is complete. Nothing needs attention right now.";
    else emptyMessage = "No upcoming assignments.";
  } else if (showData && view.active.length === 0 && view.unscheduled.length === 0 && view.counts.completed > 0 && filters.level === "all") {
    emptyMessage = "Everything with a deadline is complete.";
  }

  const stat = (value: number) => (loading && !data ? "..." : unavailable ? "Unavailable" : value);
  const stats = [
    { label: "Overdue", value: view.counts.overdue },
    { label: "Due within 24 hours", value: view.counts.within24h },
    { label: "Due in 1–3 days", value: view.counts.within72h },
    { label: "Completed", value: view.counts.completed },
  ];
  const updated = data ? new Date(data.fetched_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : null;

  return (
    <div className="page-content">
      <header className="page-header">
        <div>
          <h1 className="page-title">Priorities</h1>
          <p className="page-description">What to work on first, and why, across all your Canvas courses.</p>
        </div>
        <div className="header-actions">
          <button className="primary-button" onClick={() => void load(true)} disabled={loading}>Refresh</button>
          <button className="primary-button" onClick={() => void courseSync.startSync()} disabled={courseSync.busy} aria-busy={courseSync.busy}>
            {courseSync.busy ? "Syncing Canvas..." : "Sync Canvas"}
          </button>
        </div>
      </header>

      <dl className="priority-summary" aria-label="Priorities overview" aria-busy={loading}>
        {stats.map(({ label, value }) => (
          <div className="priority-stat" key={label}>
            <dt className="summary-label">{label}</dt>
            <dd className={`summary-value${unavailable ? " summary-state" : ""}`}>
              {stat(value)}
              {showData && partial && <span className="summary-qualifier"> (partial)</span>}
            </dd>
          </div>
        ))}
      </dl>

      {partial && (
        <p className="coverage-notice" role="status">
          Partial Canvas coverage: assignments could not be loaded for {failedNames}. Rankings and counts include loaded courses only. Use Refresh to try again.
        </p>
      )}

      <div className="priority-controls" role="group" aria-label="Filter and sort priorities">
        <label>Course
          <select value={String(filters.courseId)} onChange={(e) => setFilters({ ...filters, courseId: e.target.value === "all" ? "all" : Number(e.target.value) })}>
            <option value="all">All courses</option>
            {courses.map((c) => <option key={c.id} value={c.id}>{formatCourseCode(c.course_code, c.name || "Course")}</option>)}
          </select>
        </label>
        <label>Priority
          <select value={filters.level} onChange={(e) => setFilters({ ...filters, level: e.target.value as PriorityLevel | "all" })}>
            <option value="all">All priorities</option>
            <option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
          </select>
        </label>
        <label>Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as SortMode)}>
            <option value="recommended">Recommended priority</option>
            <option value="deadline">Earliest deadline</option>
          </select>
        </label>
        <label className="priority-check">
          <input type="checkbox" checked={filters.showCompleted} onChange={(e) => setFilters({ ...filters, showCompleted: e.target.checked })} />
          Show completed
        </label>
      </div>

      {loading && !data && <p className="list-message" role="status">Loading priorities...</p>}
      {unavailable && !loading && (
        <p className="list-message" role="alert">
          Unable to load assignments from Canvas. <button className="text-link" onClick={() => void load(true)}>Try again</button>
        </p>
      )}
      {showData && emptyMessage && <p className="list-message" role="status">{emptyMessage}</p>}

      {showData && sections.filter((section) => section.items.length > 0).map((section) => (
        <section key={section.key} className="coursework-panel" aria-labelledby={`priority-${section.key}`} aria-busy={loading}>
          <div className="panel-header">
            <h2 id={`priority-${section.key}`} className="section-title">{section.title}</h2>
            <span className="panel-note">{section.note}</span>
          </div>
          <ul className="priority-list">
            {section.items.map((item) => <PriorityCard key={`${item.course_id}-${item.id}`} item={item} />)}
          </ul>
        </section>
      ))}

      {showData && updated && (
        <p className="priority-footnote">
          Updated {updated}{data.cached ? " (recent cached data)" : ""}. Deadlines are shown in your local time zone. Each assignment is counted once in the tiles above. Estimated grade weight appears only when
          Canvas category weights and points support it.
        </p>
      )}
    </div>
  );
}
