"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { formatCourseCode } from "@/lib/course-label";
import { getCourseAccent } from "@/lib/coursework";
import { buildPriorityView, dayIndexInWeek, formatDayNumber, getPriorities, localWeekStart, resolveTimeZone } from "@/lib/priorities";
import type { PriorityItem } from "@/lib/priorities";
import { getPlans } from "@/lib/study-plans";
import type { StudyPlan } from "@/lib/study-plans";
import {
  DAYS, KIND_LABEL, courseLabel, courseTitle, createMeeting, deleteMeeting, dismissSuggestion, formatDays, formatRange, getSchedule,
  restoreSuggestions, updateMeeting,
} from "@/lib/schedule";
import type { DayCode, Meeting, MeetingInput, ScheduleCourse, ScheduleData, Suggestion } from "@/lib/schedule";
import MeetingForm from "../components/meeting-form";
import NextClassCard from "../components/next-class-card";
import ScheduleGrid from "../components/schedule-grid";
import type { GridDeadline } from "../components/schedule-grid";

const MAX_WEEKS_AHEAD = 26;

type FormState =
  | { type: "new"; courseId: number; days?: DayCode[]; note?: string; kind?: "study" }
  | { type: "edit"; courseId: number; meeting: Meeting }
  | { type: "confirm"; courseId: number; suggestion: Suggestion };

export default function ClassSchedulePage() {
  const [data, setData] = useState<ScheduleData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  // Unsubmitted, not-yet-due assignments for the "Due" row. They come from the same Priorities data and rules as the Priorities page.
  const [priorityItems, setPriorityItems] = useState<PriorityItem[] | null>(null);
  const [deadlinesFailed, setDeadlinesFailed] = useState(false);
  const [plans, setPlans] = useState<StudyPlan[]>([]);
  const [now, setNow] = useState(0);
  const [weekOffset, setWeekOffset] = useState(0);
  const zone = useMemo(() => resolveTimeZone(), []);

  useEffect(() => {
    const controller = new AbortController();
    const clock = setTimeout(() => setNow(Date.now()), 0);
    getPriorities({ signal: controller.signal }).then((payload) => {
      if (!controller.signal.aborted) setPriorityItems(payload.assignments);
    }).catch(() => { if (!controller.signal.aborted) setDeadlinesFailed(true); });   // the schedule must work without deadlines
    getPlans(controller.signal).then((payload) => { if (!controller.signal.aborted) setPlans(payload.plans); }).catch(() => {});   // and without plans
    return () => { controller.abort(); clearTimeout(clock); };
  }, []);

  const weekStart = now ? localWeekStart(now, zone) + 7 * weekOffset : 0;
  const deadlines: GridDeadline[] = useMemo(() => {
    if (!now) return [];
    const assignments: GridDeadline[] = !priorityItems ? [] : buildPriorityView(priorityItems, now, { courseId: "all", level: "all", showCompleted: false }, "due", zone).upcoming.flatMap((item) => {
      const index = item.dueMs === null ? null : dayIndexInWeek(item.dueMs, weekStart, zone);
      if (index === null || !item.due_at) return [];
      return [{ key: `${item.course_id}-${item.id}`, courseId: item.course_id, course: formatCourseCode(item.course_code, item.course_name || "Course"),
        title: item.name || "Untitled assignment", url: item.html_url, dueAt: item.due_at, dayIndex: index, kind: "assignment" as const }];
    });
    // Exams and other dates the student entered in Study Plans. Assignment plans are skipped: Canvas already supplies that deadline.
    const exams: GridDeadline[] = plans.flatMap((plan) => {
      const due = Date.parse(plan.due_at);
      const index = plan.kind !== "custom" || Number.isNaN(due) || due <= now ? null : dayIndexInWeek(due, weekStart, zone);
      if (index === null) return [];
      return [{ key: `exam-${plan.plan_id}`, courseId: plan.course_id, course: formatCourseCode(plan.course_code, plan.course_name || "Course"),
        title: plan.title, url: null, dueAt: plan.due_at, dayIndex: index, kind: "exam" as const }];
    });
    return [...assignments, ...exams];
  }, [priorityItems, plans, now, weekStart, zone]);
  // Study sessions saved from Study Plans, shown as study blocks on their date. Display only: they are not meetings.
  const sessions: Meeting[] = useMemo(() => {
    if (!now) return [];
    const clock = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: zone });
    return plans.flatMap((plan) => plan.activities.flatMap((step) => {
      if (!step.start_at || !step.end_at || step.done) return [];
      const index = dayIndexInWeek(Date.parse(step.start_at), weekStart, zone);
      if (index === null) return [];
      return [{ meeting_id: `session-${step.activity_id}`, course_id: plan.course_id, kind: "study" as const, days: [DAYS[index]], start_time: clock(step.start_at),
        end_time: clock(step.end_at), location: step.title, source: "manual" as const, source_text: null, fingerprint: null }];
    }));
  }, [plans, now, weekStart, zone]);
  const courses = useMemo(() => data?.courses ?? [], [data]);
  const gridCourses = useMemo(() => courses.map((course) => ({ ...course, meetings: [...course.meetings, ...sessions.filter((session) => session.course_id === course.course_id)] })), [courses, sessions]);
  const weekRange = now ? `${formatDayNumber(weekStart)} – ${formatDayNumber(weekStart + 6)}` : "";

  useEffect(() => {
    const controller = new AbortController();
    async function loadInitial() {
      try {
        const result = await getSchedule(controller.signal);
        if (!controller.signal.aborted) setData(result);
      } catch (error) {
        if (controller.signal.aborted) return;
        console.error("Failed to load the class schedule:", error);
        setLoadError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void loadInitial();
    return () => controller.abort();
  }, []);

  const retry = useCallback(async () => {
    setLoading(true); setLoadError(false);
    try { setData(await getSchedule()); } catch { setLoadError(true); } finally { setLoading(false); }
  }, []);

  /** Run one change, then refresh from the backend so the page always shows what is really saved. */
  async function act(action: () => Promise<unknown>, success: string, closeForm = true): Promise<void> {
    if (busy) return;
    setBusy(true); setFormError(""); setNotice("");
    try {
      await action();
      setData(await getSchedule());
      setNotice(success);
      setConfirmDelete(null);
      if (closeForm) setForm(null);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "That change could not be saved.");
    } finally { setBusy(false); }
  }

  const confirmed = courses.reduce((sum, course) => sum + course.meetings.length, 0);
  const pending = courses.reduce((sum, course) => sum + course.suggestions.length, 0);
  const sorted = [...courses].sort((a, b) => courseLabel(a).localeCompare(courseLabel(b)));

  const submit = (course: ScheduleCourse, state: FormState) => async (input: MeetingInput) => {
    if (state.type === "edit") return act(() => updateMeeting(state.meeting.meeting_id, input), "Class time updated.");
    if (state.type === "confirm") {
      return act(() => createMeeting(course.course_id, input, { fingerprint: state.suggestion.fingerprint, source_text: state.suggestion.source_text }), "Class time confirmed.");
    }
    return act(() => createMeeting(course.course_id, input), "Class time added.");
  };

  return (
    <div className="page-content">
      <header className="page-header">
        <div>
          <h1 className="page-title">Class Schedule</h1>
          <p className="page-description">Your weekly class times. Suggestions come from your syllabi, and only times you confirm are used.</p>
        </div>
      </header>

      {loading && !data && <p className="list-message" role="status">Loading your schedule...</p>}
      {loadError && !loading && (
        <p className="list-message" role="alert">
          Unable to load the class schedule. <button className="text-link" onClick={() => void retry()}>Try again</button>
        </p>
      )}
      {data && courses.length === 0 && (
        <p className="list-message" role="status">No courses are saved yet. Use Sync Canvas on the Dashboard, then come back to set up your schedule.</p>
      )}

      {data && courses.length > 0 && (
        <>
          <NextClassCard courses={courses} />
          {pending > 0 && (
            <p className="coverage-notice" role="status">
              {pending} class time{pending === 1 ? "" : "s"} found in your syllabi. Review {pending === 1 ? "it" : "them"} below; nothing is used until you confirm.
            </p>
          )}
          {notice && <p className="assistant-hint search-save-status" role="status">{notice}</p>}
          {formError && !form && <p className="coverage-notice" role="alert">{formError}</p>}

          <section className="work-section" aria-labelledby="week-title">
            <div className="work-section-head">
              <h2 id="week-title" className="section-title">{weekOffset === 0 ? "This week" : `Week of ${weekRange.split(" – ")[0]}`}</h2>
              <div className="week-tools">
                {weekRange && (
                  <div className="week-nav" role="group" aria-label="Choose week">
                    <button className="primary-button btn-ghost btn-sm" aria-label="Previous week" disabled={weekOffset === 0} onClick={() => setWeekOffset((offset) => Math.max(0, offset - 1))}>‹</button>
                    <span className="week-label" aria-live="polite">{weekRange}</span>
                    <button className="primary-button btn-ghost btn-sm" aria-label="Next week" disabled={weekOffset >= MAX_WEEKS_AHEAD} onClick={() => setWeekOffset((offset) => Math.min(MAX_WEEKS_AHEAD, offset + 1))}>›</button>
                    {weekOffset > 0 && <button className="primary-button btn-ghost btn-sm" onClick={() => setWeekOffset(0)}>This week</button>}
                  </div>
                )}
                <span className="panel-note">
                  {confirmed === 0 ? "No confirmed classes yet" : `${confirmed} confirmed class time${confirmed === 1 ? "" : "s"}`}
                  {(priorityItems || deadlines.length > 0) && ` · ${deadlines.length} deadline${deadlines.length === 1 ? "" : "s"}`}
                  {sessions.length > 0 && ` · ${sessions.length} study session${sessions.length === 1 ? "" : "s"}`}
                </span>
              </div>
            </div>
            {deadlinesFailed && <p className="assistant-hint schedule-note" role="status">Deadlines could not be loaded, so only class times are shown.</p>}
            {confirmed === 0 && deadlines.length === 0 && sessions.length === 0
              ? <p className="list-message">Confirm a suggestion or add a class time below to see your week here.</p>
              : <ScheduleGrid courses={gridCourses} deadlines={deadlines} />}
          </section>

          {sorted.map((course) => {
            const open = form && form.courseId === course.course_id ? form : null;
            const empty = course.meetings.length === 0 && course.suggestions.length === 0 && course.hints.length === 0;
            const labelId = `course-${course.course_id}`;
            return (
              <section key={course.course_id} className={`work-section schedule-course ${getCourseAccent(course.course_id)}`} aria-labelledby={labelId}>
                <div className="work-section-head">
                  <h2 id={labelId} className="section-title">{courseLabel(course)} <span className="priority-count">{courseTitle(course.name)}</span></h2>
                  <div className="meeting-buttons">
                    <button className="primary-button btn-ghost btn-sm" disabled={busy} onClick={() => { setFormError(""); setForm({ type: "new", courseId: course.course_id }); }}>Add class time</button>
                    <button className="primary-button btn-ghost btn-sm" disabled={busy} onClick={() => { setFormError(""); setForm({ type: "new", courseId: course.course_id, kind: "study" }); }}>Add study block</button>
                  </div>
                </div>

                <ul className="meeting-list">
                  {course.meetings.map((meeting) => (
                    <li key={meeting.meeting_id}>
                      <div>
                        {meeting.kind === "study" && <span className="study-tag">Study block</span>} <strong>{formatDays(meeting.days)}</strong> · {formatRange(meeting.start_time, meeting.end_time)}
                        {meeting.location && <span> · {meeting.location}</span>}
                        <span className="meeting-meta">
                          {meeting.kind === "study" ? "Your own study time" : KIND_LABEL[meeting.kind]} · {meeting.source === "syllabus" ? "Confirmed from your syllabus" : "Added by you"}
                        </span>
                      </div>
                      <div className="meeting-buttons">
                        {confirmDelete === meeting.meeting_id ? (
                          <>
                            <span>Delete this class time?</span>
                            <button className="primary-button" disabled={busy} onClick={() => void act(() => deleteMeeting(meeting.meeting_id), "Class time deleted.", false)}>Yes, delete</button>
                            <button className="primary-button" disabled={busy} onClick={() => setConfirmDelete(null)}>Keep it</button>
                          </>
                        ) : (
                          <>
                            <button className="text-link" disabled={busy} onClick={() => { setFormError(""); setForm({ type: "edit", courseId: course.course_id, meeting }); }}>Edit</button>
                            <button className="text-link text-danger" disabled={busy} onClick={() => setConfirmDelete(meeting.meeting_id)}>Delete</button>
                          </>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>

                {course.suggestions.map((suggestion) => (
                  <div key={suggestion.fingerprint} className="suggestion">
                    <p className="suggestion-label">Found in your syllabus{suggestion.confidence === "medium" ? " (check this one)" : ""}</p>
                    <p><strong>{formatDays(suggestion.days)}</strong> · {formatRange(suggestion.start_time, suggestion.end_time)}{suggestion.location ? ` · ${suggestion.location}` : ""}</p>
                    <blockquote>“{suggestion.source_text}”</blockquote>
                    <div className="meeting-buttons">
                      <button className="primary-button" disabled={busy} onClick={() => void act(() => createMeeting(course.course_id, {
                        days: suggestion.days, start_time: suggestion.start_time, end_time: suggestion.end_time, kind: suggestion.kind, location: suggestion.location,
                      }, { fingerprint: suggestion.fingerprint, source_text: suggestion.source_text }), "Class time confirmed.", false)}>Confirm</button>
                      <button className="primary-button" disabled={busy} onClick={() => { setFormError(""); setForm({ type: "confirm", courseId: course.course_id, suggestion }); }}>Edit first</button>
                      <button className="text-link" disabled={busy} onClick={() => void act(() => dismissSuggestion(course.course_id, suggestion.fingerprint), "Suggestion dismissed.", false)}>Not right</button>
                    </div>
                  </div>
                ))}

                {course.hints.map((hint) => (
                  <div key={hint.source_text} className="suggestion">
                    <p className="suggestion-label">Your syllabus mentions days but no time</p>
                    <blockquote>“{hint.source_text}”</blockquote>
                    <button className="primary-button" disabled={busy} onClick={() => { setFormError(""); setForm({ type: "new", courseId: course.course_id, days: hint.days, note: "Days came from your syllabus. Add the time." }); }}>Add the time</button>
                  </div>
                ))}

                {empty && !open && <p className="list-message">No class time yet. Use “Add class time” to enter it yourself.</p>}

                {open && (
                  <MeetingForm
                    key={open.type === "edit" ? open.meeting.meeting_id : open.type === "confirm" ? open.suggestion.fingerprint : `new-${course.course_id}`}
                    title={open.type === "edit" ? (open.meeting.kind === "study" ? "Edit study block" : "Edit class time") : open.type === "confirm" ? "Edit before confirming" : open.kind === "study" ? "Add study block" : "Add class time"}
                    submitLabel={open.type === "edit" ? "Save changes" : open.type === "confirm" ? "Confirm" : open.kind === "study" ? "Add study block" : "Add class time"}
                    note={open.type === "new" ? open.note : undefined}
                    initial={open.type === "edit" ? open.meeting : open.type === "confirm" ? open.suggestion : open.kind === "study" ? { kind: "study" } : open.days ? { days: open.days } : undefined}
                    onSubmit={submit(course, open)} onCancel={() => { setForm(null); setFormError(""); }} error={formError} busy={busy} />
                )}

                {course.dismissed_count > 0 && (
                  <p className="meeting-restore">
                    <button className="text-link" disabled={busy} onClick={() => void act(() => restoreSuggestions(course.course_id), "Dismissed suggestions restored.", false)}>
                      Restore {course.dismissed_count} dismissed suggestion{course.dismissed_count === 1 ? "" : "s"}
                    </button>
                  </p>
                )}
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}
