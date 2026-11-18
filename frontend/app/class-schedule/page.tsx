"use client";

import { useCallback, useEffect, useState } from "react";

import { getCourseAccent } from "@/lib/coursework";
import {
  KIND_LABEL, courseLabel, courseTitle, createMeeting, deleteMeeting, dismissSuggestion, formatDays, formatRange, getSchedule,
  restoreSuggestions, updateMeeting,
} from "@/lib/schedule";
import type { DayCode, Meeting, MeetingInput, ScheduleCourse, ScheduleData, Suggestion } from "@/lib/schedule";
import MeetingForm from "../components/meeting-form";
import NextClassCard from "../components/next-class-card";
import ScheduleGrid from "../components/schedule-grid";

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

  const courses = data?.courses ?? [];
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
              <h2 id="week-title" className="section-title">This week</h2>
              <span className="panel-note">{confirmed === 0 ? "No confirmed classes yet" : `${confirmed} confirmed class time${confirmed === 1 ? "" : "s"}`}</span>
            </div>
            {confirmed === 0
              ? <p className="list-message">Confirm a suggestion or add a class time below to see your week here.</p>
              : <ScheduleGrid courses={courses} />}
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
