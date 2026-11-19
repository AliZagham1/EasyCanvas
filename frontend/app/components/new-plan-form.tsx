"use client";

import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { formatCourseCode } from "@/lib/course-label";
import { formatDueShort } from "@/lib/coursework";
import { localToIso } from "@/lib/study-plans";
import type { NewPlanInput } from "@/lib/study-plans";
import type { RankedItem } from "@/lib/priorities";

type CourseOption = { id: number; course_code: string | null; name: string | null };

/** Start a plan for a Canvas assignment (title and deadline come from Canvas) or for an exam or other item with a date you enter. */
export default function NewPlanForm({ assignments, courses, planned, busy, error, onCreate, onCancel }: {
  assignments: RankedItem[]; courses: CourseOption[]; planned: Set<string>; busy: boolean; error: string;
  onCreate: (input: NewPlanInput) => void; onCancel: () => void;
}) {
  const available = useMemo(() => assignments.filter((item) => !planned.has(`${item.course_id}:${item.id}`)), [assignments, planned]);
  const [mode, setMode] = useState<"assignment" | "custom">(available.length ? "assignment" : "custom");
  const [pick, setPick] = useState("");
  const [title, setTitle] = useState("");
  const [courseId, setCourseId] = useState(courses[0] ? String(courses[0].id) : "");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("09:00");
  const [problem, setProblem] = useState("");

  const groups = useMemo(() => {
    const byCourse = new Map<number, RankedItem[]>();
    for (const item of available) byCourse.set(item.course_id, [...(byCourse.get(item.course_id) ?? []), item]);
    return [...byCourse.entries()];
  }, [available]);

  function submit(event: FormEvent) {
    event.preventDefault();
    setProblem("");
    if (mode === "assignment") {
      const chosen = available.find((item) => `${item.course_id}:${item.id}` === (pick || `${available[0]?.course_id}:${available[0]?.id}`));
      if (!chosen) { setProblem("Choose an assignment."); return; }
      onCreate({ course_id: chosen.course_id, assignment_id: chosen.id });
      return;
    }
    const due = localToIso(date, time);
    if (!title.trim()) { setProblem("Give the plan a name, such as “Midterm exam”."); return; }
    if (!courseId) { setProblem("Choose a course."); return; }
    if (!due) { setProblem("Enter the date and time."); return; }
    onCreate({ course_id: Number(courseId), title: title.trim(), due_at: due });
  }

  return <form className="plan-form plan-card" onSubmit={submit} aria-label="New study plan">
    <header className="plan-header"><h2 className="plan-title">New plan</h2></header>
    <fieldset className="plan-mode">
      <legend>Plan for</legend>
      <label><input type="radio" name="plan-mode" checked={mode === "assignment"} disabled={!available.length} onChange={() => setMode("assignment")} /> A Canvas assignment</label>
      <label><input type="radio" name="plan-mode" checked={mode === "custom"} onChange={() => setMode("custom")} /> An exam or other date</label>
    </fieldset>
    {mode === "assignment" ? (
      <label className="plan-field">Assignment
        <select value={pick} onChange={(event) => setPick(event.target.value)}>
          {groups.map(([course, items]) => (
            <optgroup key={course} label={formatCourseCode(items[0].course_code, items[0].course_name || "Course")}>
              {items.map((item) => <option key={item.id} value={`${item.course_id}:${item.id}`}>{item.name} — due {formatDueShort(item.due_at)}</option>)}
            </optgroup>
          ))}
        </select>
        <span className="assistant-hint">The title and deadline come from Canvas. Submitted work and work with no due date are not listed.</span>
      </label>
    ) : (
      <div className="plan-custom">
        <label className="plan-field">Name<input value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} placeholder="Midterm exam" /></label>
        <label className="plan-field">Course
          <select value={courseId} onChange={(event) => setCourseId(event.target.value)}>
            {courses.map((course) => <option key={course.id} value={course.id}>{formatCourseCode(course.course_code, course.name || "Course")}</option>)}
          </select>
        </label>
        <label className="plan-field">Date<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        <label className="plan-field">Time<input type="time" value={time} onChange={(event) => setTime(event.target.value)} /></label>
      </div>
    )}
    {(problem || error) && <p className="coverage-notice" role="alert">{problem || error}</p>}
    <div className="plan-actions">
      <button type="submit" className="primary-button btn-primary" disabled={busy}>Create plan</button>
      <button type="button" className="primary-button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}
