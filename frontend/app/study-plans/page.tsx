"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";

import { buildPriorityView, getPriorities, resolveTimeZone } from "@/lib/priorities";
import type { PrioritiesPayload } from "@/lib/priorities";
import {
  TimeConflict, acceptTimes, addStep, clearStepTime, createPlan, deletePlan, deleteStep, getPlans, savePreferences, savedTimeMessage, setStepTime, suggestTimes, updateStep,
} from "@/lib/study-plans";
import type { Conflict, NewPlanInput, PlanPreferences, PlanStep, Proposal, StudyPlan } from "@/lib/study-plans";
import NewPlanForm from "../components/new-plan-form";
import StudyPlanCard from "../components/study-plan-card";

export default function StudyPlansPage() {
  const [plans, setPlans] = useState<StudyPlan[] | null>(null);
  const [prefs, setPrefs] = useState<PlanPreferences | null>(null);
  const [canvas, setCanvas] = useState<PrioritiesPayload | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [now, setNow] = useState(0);
  const [creating, setCreating] = useState(false);
  const [proposals, setProposals] = useState<Record<string, Proposal>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const zone = useMemo(() => resolveTimeZone(), []);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const payload = await getPlans(signal);
    if (signal?.aborted) return;
    setPlans(payload.plans); setPrefs(payload.preferences); setNow(Date.now());
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function loadInitial() {
      try {
        const payload = await getPlans(controller.signal);
        if (controller.signal.aborted) return;
        setPlans(payload.plans); setPrefs(payload.preferences); setNow(Date.now());
      } catch { if (!controller.signal.aborted) setLoadError(true); }
    }
    void loadInitial();
    // The assignment list is optional: without it, plans for exams and other dates still work.
    getPriorities({ signal: controller.signal }).then((payload) => { if (!controller.signal.aborted) setCanvas(payload); }).catch(() => {});
    return () => controller.abort();
  }, []);

  /** Run one change, then reload from the backend so the page always shows what is really saved. */
  async function act(action: () => Promise<unknown>, success = "") {
    if (busy) return false;
    setBusy(true); setError(""); setNotice("");
    try { await action(); await refresh(); if (success) setNotice(success); return true; }
    catch (problem) { setError(problem instanceof Error ? problem.message : "That change could not be saved."); return false; }
    finally { setBusy(false); }
  }

  const upcoming = useMemo(() => canvas && now
    ? buildPriorityView(canvas.assignments, now, { courseId: "all", level: "all", showCompleted: false }, "due", zone).upcoming : [], [canvas, now, zone]);
  const planned = useMemo(() => new Set((plans ?? []).filter((plan) => plan.assignment_id !== null).map((plan) => `${plan.course_id}:${plan.assignment_id}`)), [plans]);

  async function create(input: NewPlanInput) {
    if (await act(() => createPlan(input), "Plan created. Add the steps you need, then ask for times.")) setCreating(false);
  }
  const forget = (id: string) => setProposals((current) => { const { [id]: _gone, ...rest } = current; return rest; });  // eslint-disable-line @typescript-eslint/no-unused-vars
  async function suggest(plan: StudyPlan) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { const proposal = await suggestTimes(plan.plan_id); setProposals((current) => ({ ...current, [plan.plan_id]: proposal })); }
    catch (problem) { setError(problem instanceof Error ? problem.message : "Times could not be suggested."); }
    finally { setBusy(false); }
  }
  /** The student picks a time. A booked time comes back as conflicts so the card can warn and offer "Add anyway". */
  async function chooseTime(plan: StudyPlan, step: PlanStep, startIso: string, replace: boolean): Promise<{ ok: true } | { conflicts: Conflict[] } | { error: string }> {
    if (busy) return { error: "Please wait a moment and try again." };
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await setStepTime(plan.plan_id, step.activity_id, startIso, replace);
      await refresh();
      setProposals({});                                   // other proposed times may now be stale
      setNotice(savedTimeMessage(result));
      return { ok: true };
    } catch (problem) {
      if (problem instanceof TimeConflict) return { conflicts: problem.conflicts };
      return { error: problem instanceof Error ? problem.message : "That time could not be saved." };
    } finally { setBusy(false); }
  }
  async function accept(plan: StudyPlan) {
    const proposal = proposals[plan.plan_id];
    if (proposal && await act(() => acceptTimes(plan.plan_id, proposal.placements), "Times saved. They now appear on your Class Schedule.")) forget(plan.plan_id);
  }

  return <div className="page-content">
    <header className="page-header">
      <div>
        <h1 className="page-title">Study Plans</h1>
        <p className="page-description">Break an assignment or exam into steps and find time for them. Nothing here uses AI.</p>
      </div>
      {plans && !creating && <button type="button" className="primary-button btn-primary" onClick={() => { setError(""); setCreating(true); }}>New plan</button>}
    </header>

    {loadError && <p className="list-message" role="alert">Unable to load study plans. Check that the backend is running, then reload.</p>}
    {!plans && !loadError && <p className="list-message" role="status">Loading your plans...</p>}
    {notice && <p className="assistant-hint search-save-status" role="status">{notice}</p>}
    {error && !creating && <p className="coverage-notice" role="alert">{error}</p>}

    {plans && creating && <NewPlanForm assignments={upcoming} courses={canvas?.courses ?? []} planned={planned} busy={busy} error={error}
      onCreate={(input) => void create(input)} onCancel={() => { setCreating(false); setError(""); }} />}

    {plans && plans.length === 0 && !creating && (
      <div className="plan-empty"><h2>No plans yet</h2>
        <p>Pick an assignment or add an exam date, list the steps you need, and EasyCanvas will find time for them around your classes.</p>
        <button type="button" className="primary-button btn-primary" onClick={() => setCreating(true)}>New plan</button></div>
    )}

    {plans?.map((plan) => (
      <StudyPlanCard key={plan.plan_id} plan={plan} proposal={proposals[plan.plan_id]} now={now} zone={zone} busy={busy}
        onAddStep={(title, minutes) => void act(() => addStep(plan.plan_id, { title, minutes })).then(() => forget(plan.plan_id))}
        onToggle={(step: PlanStep) => void act(() => updateStep(plan.plan_id, step.activity_id, { done: !step.done }))}
        onRemoveStep={(step: PlanStep) => void act(() => deleteStep(plan.plan_id, step.activity_id)).then(() => forget(plan.plan_id))}
        onClearTime={(step: PlanStep) => void act(() => clearStepTime(plan.plan_id, step.activity_id), "Time cleared.")}
        onSetTime={(step: PlanStep, startIso: string, replace: boolean) => chooseTime(plan, step, startIso, replace)}
        onSuggest={() => void suggest(plan)} onAccept={() => void accept(plan)} onDiscard={() => forget(plan.plan_id)}
        onDelete={() => void act(() => deletePlan(plan.plan_id), "Plan deleted.").then(() => forget(plan.plan_id))} />
    ))}

    {prefs && <Preferences prefs={prefs} busy={busy} onSave={(next) => act(async () => setPrefs(await savePreferences(next)), "Preferences saved. New suggestions will use them.")} />}
  </div>;
}

function Preferences({ prefs, busy, onSave }: { prefs: PlanPreferences; busy: boolean; onSave: (next: PlanPreferences) => Promise<boolean> }) {
  const [draft, setDraft] = useState(prefs);
  const set = (key: keyof PlanPreferences, value: string) => setDraft((current) => ({ ...current, [key]: key.startsWith("day_") ? value : Number(value) }));
  function submit(event: FormEvent) { event.preventDefault(); void onSave(draft); }
  return <details className="plan-prefs">
    <summary>Planning preferences</summary>
    <form onSubmit={submit}>
      <p className="assistant-hint">Times are only suggested inside your study hours, never over a class or another study session.</p>
      <div className="plan-custom">
        <label className="plan-field">Study hours start<input type="time" value={draft.day_start} onChange={(event) => set("day_start", event.target.value)} /></label>
        <label className="plan-field">Study hours end<input type="time" value={draft.day_end} onChange={(event) => set("day_end", event.target.value)} /></label>
        <label className="plan-field">Most study per day (minutes)<input type="number" min={30} max={720} step={15} value={draft.max_minutes_per_day} onChange={(event) => set("max_minutes_per_day", event.target.value)} /></label>
        <label className="plan-field">Finish before the deadline (minutes)<input type="number" min={0} max={1440} step={30} value={draft.buffer_minutes} onChange={(event) => set("buffer_minutes", event.target.value)} /></label>
      </div>
      <button type="submit" className="primary-button" disabled={busy}>Save preferences</button>
    </form>
  </details>;
}
