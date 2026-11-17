"use client";
import { useState } from "react";
import { DAYS, DAY_LONG, DAY_SHORT, KIND_LABEL } from "@/lib/schedule";
import type { DayCode, MeetingInput, MeetingKind } from "@/lib/schedule";

type Props = {
  title: string; submitLabel: string; initial?: Partial<MeetingInput>; note?: string;
  onSubmit: (input: MeetingInput) => Promise<void>; onCancel: () => void; error?: string; busy?: boolean;
};

/** Add or edit one class time. Checks the obvious mistakes up front; the backend still validates everything. */
export default function MeetingForm({ title, submitLabel, initial, note, onSubmit, onCancel, error, busy }: Props) {
  const [days, setDays] = useState<DayCode[]>(initial?.days ?? []);
  const [start, setStart] = useState(initial?.start_time ?? "");
  const [end, setEnd] = useState(initial?.end_time ?? "");
  const [location, setLocation] = useState(initial?.location ?? "");
  const [kind, setKind] = useState<MeetingKind>(initial?.kind ?? "lecture");
  const [problem, setProblem] = useState("");

  const toggle = (day: DayCode) => setDays((current) => current.includes(day) ? current.filter((d) => d !== day) : [...current, day]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (days.length === 0) return setProblem("Choose at least one day.");
    if (!start || !end) return setProblem("Enter a start and an end time.");
    if (start >= end) return setProblem("The end time must be after the start time.");
    setProblem("");
    await onSubmit({ days: DAYS.filter((d) => days.includes(d)), start_time: start, end_time: end, kind, location: location.trim() || null });
  }

  const shown = problem || error;
  return (
    <form className="meeting-form" onSubmit={submit} aria-label={title} noValidate>
      <h4>{title}</h4>
      {note && <p className="assistant-hint">{note}</p>}
      <fieldset>
        <legend>Days</legend>
        <div className="day-toggles">
          {DAYS.map((day) => (
            <label key={day} className={days.includes(day) ? "day-toggle day-toggle-on" : "day-toggle"} title={DAY_LONG[day]}>
              <input type="checkbox" checked={days.includes(day)} onChange={() => toggle(day)} aria-label={DAY_LONG[day]} disabled={busy} />
              {DAY_SHORT[day]}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="meeting-fields">
        <label>Start<input type="time" value={start} onChange={(e) => setStart(e.target.value)} disabled={busy} /></label>
        <label>End<input type="time" value={end} onChange={(e) => setEnd(e.target.value)} disabled={busy} /></label>
        <label>Location<input type="text" value={location} maxLength={120} placeholder="Room (optional)" onChange={(e) => setLocation(e.target.value)} disabled={busy} /></label>
        <label>Type
          <select value={kind} onChange={(e) => setKind(e.target.value as MeetingKind)} disabled={busy}>
            {(Object.keys(KIND_LABEL) as MeetingKind[]).map((value) => <option key={value} value={value}>{KIND_LABEL[value]}</option>)}
          </select>
        </label>
      </div>
      {shown && <p className="coverage-notice" role="alert">{shown}</p>}
      <div className="meeting-actions">
        <button type="submit" className="primary-button" disabled={busy}>{busy ? "Saving..." : submitLabel}</button>
        <button type="button" className="primary-button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}
