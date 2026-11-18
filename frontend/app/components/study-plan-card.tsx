"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { formatCourseCode } from "@/lib/course-label";
import { formatDueDate, formatDueShort } from "@/lib/coursework";
import { describeDue } from "@/lib/priorities";
import { STEP_LENGTHS, defaultPickerTime, endTimeText, formatMinutes, formatSession, localToIso, pickerTimeFor, planSummary } from "@/lib/study-plans";
import type { Conflict, PlanStep, Proposal, StudyPlan } from "@/lib/study-plans";

type Props = {
  plan: StudyPlan; proposal: Proposal | undefined; now: number; zone: string; busy: boolean;
  onAddStep: (title: string, minutes: number) => void; onToggle: (step: PlanStep) => void; onRemoveStep: (step: PlanStep) => void;
  onClearTime: (step: PlanStep) => void; onSetTime: (step: PlanStep, startIso: string, replace: boolean) => Promise<{ ok: true } | { conflicts: Conflict[] } | { error: string }>;
  onSuggest: () => void; onAccept: () => void; onDiscard: () => void; onDelete: () => void;
};

/** One plan: its steps, a form to add more, and proposed times to review. Times are saved only when accepted. */
export default function StudyPlanCard({ plan, proposal, now, zone, busy, onAddStep, onToggle, onRemoveStep, onClearTime, onSetTime, onSuggest, onAccept, onDiscard, onDelete }: Props) {
  const [title, setTitle] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [confirming, setConfirming] = useState(false);
  const [picking, setPicking] = useState<PlanStep | null>(null);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
  const [pickError, setPickError] = useState("");
  const dueMs = Date.parse(plan.due_at);
  const passed = now > 0 && dueMs <= now;
  const waiting = plan.activities.filter((step) => !step.done && !step.start_at).length;
  const names = new Map(plan.activities.map((step) => [step.activity_id, step.title]));
  const labelId = `plan-${plan.plan_id}`;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    onAddStep(title.trim(), minutes);
    setTitle("");
  }

  function openPicker(step: PlanStep) {
    const start = step.start_at ? pickerTimeFor(step.start_at) : defaultPickerTime();
    setPicking(step); setDate(start.date); setTime(start.time); setConflicts(null); setPickError("");
  }
  function closePicker() { setPicking(null); setConflicts(null); setPickError(""); }
  async function savePick(replace: boolean) {
    if (!picking) return;
    const iso = localToIso(date, time);
    if (!iso) { setPickError("Choose a date and a start time."); return; }
    setPickError("");
    const outcome = await onSetTime(picking, iso, replace);
    if ("ok" in outcome) closePicker();
    else if ("conflicts" in outcome) setConflicts(outcome.conflicts);
    else { setConflicts(null); setPickError(outcome.error); }
  }

  return <section className="work-section plan" aria-labelledby={labelId}>
    <div className="work-section-head">
      <div>
        <h2 id={labelId} className="section-title">{plan.title}</h2>
        <p className="plan-meta">
          {formatCourseCode(plan.course_code, plan.course_name || "Course")} · {plan.kind === "assignment" ? "Due" : "On"}{" "}
          <time dateTime={plan.due_at} title={formatDueDate(plan.due_at)}>{formatDueShort(plan.due_at, now || dueMs)}</time>
          {now > 0 && <span className={passed ? "plan-passed" : undefined}> · {passed ? "Deadline passed" : describeDue(dueMs, now, zone)}</span>}
        </p>
      </div>
      <span className="panel-note">{planSummary(plan)}</span>
    </div>

    {plan.activities.length > 0 && <ul className="work-list">
      {plan.activities.map((step) => (
        <li key={step.activity_id} className="work-row plan-step">
          <div className="work-main">
            <label className="plan-check">
              <input type="checkbox" checked={step.done} disabled={busy} onChange={() => onToggle(step)} aria-label={`${step.title}: ${step.done ? "mark not done" : "mark done"}`} />
              <span className={step.done ? "is-done" : undefined}>{step.title}</span>
            </label>
            <p className="work-meta">{formatMinutes(step.minutes)}</p>
          </div>
          <div className="work-due plan-when">
            {step.start_at && step.end_at
              ? <><span>{formatSession(step.start_at, step.end_at)}</span><button type="button" className="text-link" disabled={busy} onClick={() => onClearTime(step)}>Clear time</button></>
              : <span className="work-relative">{step.done ? "Done" : "Not scheduled"}</span>}
            {!step.done && <button type="button" className="text-link" disabled={busy} onClick={() => openPicker(step)} aria-label={`${step.start_at ? "Change" : "Pick"} the time for ${step.title}`}>{step.start_at ? "Change time" : "Pick time"}</button>}
            <button type="button" className="text-link text-danger" disabled={busy} onClick={() => onRemoveStep(step)} aria-label={`Remove step ${step.title}`}>Remove</button>
          </div>
        </li>
      ))}
    </ul>}

    {picking && <form className="plan-pick" role="group" aria-label={`Choose a time for ${picking.title}`} onSubmit={(event) => { event.preventDefault(); void savePick(false); }}>
      <p><strong>When will you study “{picking.title}”?</strong> It takes {formatMinutes(picking.minutes)}.</p>
      <div className="plan-pick-fields">
        <label className="plan-field">Date<input type="date" value={date} onChange={(event) => { setDate(event.target.value); setConflicts(null); }} /></label>
        <label className="plan-field">Start<input type="time" value={time} onChange={(event) => { setTime(event.target.value); setConflicts(null); }} /></label>
        {endTimeText(date, time, picking.minutes) && <p className="assistant-hint">Ends at {endTimeText(date, time, picking.minutes)}</p>}
      </div>
      {conflicts && <div className="coverage-notice" role="alert">
        <p><strong>You already have something at this time:</strong></p>
        <ul>{conflicts.map((item, index) => <li key={index}>{item.title} · {item.when}</li>)}</ul>
        <p>Adding it anyway keeps classes and study blocks where they are, and replaces any other study session at that time.</p>
      </div>}
      {pickError && <p className="coverage-notice" role="alert">{pickError}</p>}
      <div className="plan-actions">
        {conflicts
          ? <><button type="button" className="primary-button btn-primary" disabled={busy} onClick={() => void savePick(true)}>Add anyway</button>
              <button type="button" className="primary-button" disabled={busy} onClick={() => setConflicts(null)}>Choose another time</button></>
          : <button type="submit" className="primary-button btn-primary" disabled={busy}>Save time</button>}
        <button type="button" className="primary-button btn-ghost" disabled={busy} onClick={closePicker}>Cancel</button>
      </div>
    </form>}

    <form className="plan-add" onSubmit={submit} aria-label={`Add a step to ${plan.title}`}>
      <label className="sr-only" htmlFor={`${labelId}-step`}>Step</label>
      <input id={`${labelId}-step`} value={title} maxLength={120} placeholder="Add a step, for example “Read chapter 4”" onChange={(event) => setTitle(event.target.value)} />
      <label className="sr-only" htmlFor={`${labelId}-minutes`}>Time for this step</label>
      <select id={`${labelId}-minutes`} value={minutes} onChange={(event) => setMinutes(Number(event.target.value))}>
        {STEP_LENGTHS.map((length) => <option key={length} value={length}>{formatMinutes(length)}</option>)}
      </select>
      <button type="submit" className="primary-button" disabled={busy || !title.trim()}>Add step</button>
    </form>

    {proposal && <div className="plan-proposal" role="region" aria-label="Proposed times">
      <h3>Proposed times</h3>
      {proposal.placements.length > 0 && <ul>
        {proposal.placements.map((placement) => <li key={placement.activity_id}><strong>{names.get(placement.activity_id)}</strong> · {formatSession(placement.start_at, placement.end_at)}</li>)}
      </ul>}
      {proposal.unplaced.length > 0 && <div className="coverage-notice" role="alert">
        <p>Not enough free time for {proposal.unplaced.length === 1 ? "one step" : `${proposal.unplaced.length} steps`}: you need {formatMinutes(proposal.needed_minutes)} and have {formatMinutes(proposal.free_minutes)} open before the deadline.</p>
        <ul>{proposal.unplaced.map((item) => <li key={item.activity_id}>{names.get(item.activity_id)}: {item.reason}</li>)}</ul>
      </div>}
      {proposal.placements.length === 0 && proposal.unplaced.length === 0 && <p className="assistant-hint">Every open step already has a time.</p>}
      <div className="plan-actions">
        {proposal.placements.length > 0 && <button type="button" className="primary-button btn-primary" disabled={busy} onClick={onAccept}>Save these times</button>}
        <button type="button" className="primary-button" disabled={busy} onClick={onDiscard}>Discard</button>
      </div>
    </div>}

    <div className="plan-actions">
      <button type="button" className="primary-button" disabled={busy || passed || waiting === 0} onClick={onSuggest}>Suggest times</button>
      {confirming
        ? <><span className="assistant-hint">Delete this plan and its steps?</span>
            <button type="button" className="primary-button btn-danger" disabled={busy} onClick={onDelete}>Yes, delete</button>
            <button type="button" className="primary-button" disabled={busy} onClick={() => setConfirming(false)}>Keep it</button></>
        : <button type="button" className="text-link text-danger" disabled={busy} onClick={() => setConfirming(true)}>Delete plan</button>}
    </div>
  </section>;
}
