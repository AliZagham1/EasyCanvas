"use client";
import { useEffect, useRef, useState } from "react";
import { formatCourseCode } from "@/lib/course-label";
import { compactSearchStatus, formatApiCost, getSearchCourses, getSearchSettings, saveSearchSettings, updateSearch, usageWarningProgress } from "@/lib/search-index";
import type { CourseIndex, SearchPreferences } from "@/lib/search-index";

export default function SearchSettings() {
  const [preferences, setPreferences] = useState<SearchPreferences | null>(null);
  const [courses, setCourses] = useState<CourseIndex[]>([]);
  const [warning, setWarning] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const saving = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load(initial = false) {
      let updating = false;
      try {
        const [data, result] = await Promise.all([getSearchSettings(controller.signal), getSearchCourses(controller.signal)]);
        updating = result.courses.some(course => ["updating", "syncing"].includes(course.status));
        if (!controller.signal.aborted && !saving.current) {
          setPreferences(data); setCourses(result.courses); setError("");
          if (initial) setWarning(data.settings.monthly_warning_usd?.toString() || "");
        }
      } catch { if (!controller.signal.aborted) setError("Unable to load search settings and usage. Check the backend and retry."); }
      finally { if (!controller.signal.aborted) timer = setTimeout(() => void load(), updating ? 3000 : 10000); }
    }
    void load(true);
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);

  async function save(changes: {auto_index?: boolean; monthly_warning_usd?: number | null}) {
    if (saving.current) return;
    const previous = preferences;
    saving.current = true;
    if (preferences) setPreferences({...preferences, settings: {...preferences.settings, ...changes}});
    setBusy(true); setError(""); setMessage("");
    try {
      const data = await saveSearchSettings(changes);
      setPreferences(data);
      setMessage("Search settings saved.");
      if (changes.auto_index !== undefined) {
        // Only indicate queued work after the backend has persisted and accepted ON.
        setCourses(current => current.map(course => ({...course, automatic: changes.auto_index!,
          status: changes.auto_index && course.remaining_chunks > 0 && course.status !== "syncing" ? "updating" : course.status})));
        try { setCourses((await getSearchCourses()).courses); }
        catch { setError("Your setting was saved. Course search status could not be refreshed; it will retry automatically."); }
      }
    } catch { setPreferences(previous); setError("Search settings could not be saved. Please retry."); }
    finally { saving.current = false; setBusy(false); }
  }

  async function update(courseId: number) {
    if (saving.current) return;
    saving.current = true;
    setBusy(true); setError(""); setMessage("");
    try {
      const status = await updateSearch(courseId);
      setCourses(current => current.map(course => course.course_id === courseId ? status : course));
      setMessage("Course search update queued.");
    } catch { setError("Course search could not be updated. Please retry."); }
    finally { saving.current = false; setBusy(false); }
  }

  const usage = preferences?.usage;
  const limit = preferences?.settings.monthly_warning_usd ?? null;
  const progress = usage ? usageWarningProgress(usage.this_month.estimated_cost_usd, limit) : null;
  const began = usage?.recording_started_at && !Number.isNaN(Date.parse(usage.recording_started_at))
    ? new Date(usage.recording_started_at).toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"}) : null;
  // Older backends expose lifetime category totals only; label that fallback honestly.
  const breakdown = usage?.monthly_breakdown || usage;

  return <section className="search-settings" aria-labelledby="search-settings-title">
    <h2 id="search-settings-title" className="section-title">Study Assistant search</h2>
    {error && <p className="coverage-notice" role="alert">{error}</p>}
    {message && <p className="assistant-hint search-save-status" role="status">{message}</p>}
    {!preferences ? !error && <p role="status">Loading search settings...</p> : <>
      <div className="search-auto-option">
        <label className="search-auto-control">
          <span>Keep Study Assistant search automatically updated</span>
          <span className="search-switch">
            <input type="checkbox" role="switch" checked={preferences.settings.auto_index} disabled={busy} aria-describedby="automatic-search-help"
              onChange={event => void save({auto_index: event.target.checked})} />
            <span className="search-switch-track" aria-hidden="true" />
          </span>
        </label>
        <p id="automatic-search-help" className="assistant-hint">Indexes only new or changed material using your OpenAI credits. Turn off to update courses manually.</p>
      </div>
      {courses.length > 0 ? <div className="coursework-panel search-course-list" aria-label="Course search readiness">{courses.map(course => {
        const title = formatCourseCode(course.title, course.title);
        return <details key={course.course_id} className={`search-course search-course-${course.status}`}>
          <summary>
            <span className="search-course-title" title={course.title}>{title}</span>
            <span className="search-course-count" aria-label={`${course.indexed_chunks.toLocaleString()} of ${course.eligible_chunks.toLocaleString()} passages searchable`}>{course.indexed_chunks.toLocaleString()} / {course.eligible_chunks.toLocaleString()}</span>
            <span className="search-course-state">{compactSearchStatus(course)}</span>
            <svg className="search-disclosure" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m7 5 5 5-5 5" /></svg>
            {course.eligible_chunks > 0 && course.remaining_chunks > 0 && <progress className="search-progress course-index-progress" max={course.eligible_chunks} value={course.indexed_chunks} aria-label={`${title} search progress`} />}
          </summary>
          <div className="search-course-detail">
            <div className="search-course-actions"><p className="assistant-hint">{course.pending_chunks} pending · {course.failed_chunks} failed<br />
              Last completed: {course.last_successful_at ? new Date(course.last_successful_at).toLocaleString() : "Not recorded yet"}</p>
              {(course.remaining_chunks > 0 || !course.automatic) && <button type="button" className="primary-button" disabled={busy || course.status === "updating"}
                onClick={() => void update(course.course_id)}>{course.status === "failed" ? "Retry indexing" : "Update course search"}</button>}
            </div>
            <ul className="search-document-list">{course.documents.filter(doc => doc.eligible_chunks > 0).map(doc => <li key={doc.document_key}>
              <span>{doc.title || "Untitled document"}</span><span>{doc.indexed_chunks} / {doc.eligible_chunks} searchable{doc.failed_chunks > 0 ? ` · ${doc.failed_chunks} failed` : ""}</span>
            </li>)}</ul>
          </div>
        </details>;
      })}</div> : <p className="assistant-hint">No current course materials have been saved yet. Sync Canvas to get started.</p>}

      {usage && breakdown && <section className="api-usage-panel" aria-labelledby="api-usage-title">
        <h3 id="api-usage-title" className="section-title">EasyCanvas API Usage</h3>
        <div className="api-usage-headline">
          <p className="api-usage-amount"><strong>{formatApiCost(usage.this_month.estimated_cost_usd)}</strong> <span>tracked this month</span></p>
          {limit !== null && <span className="api-usage-warning">/ {formatApiCost(limit)} warning</span>}
        </div>
        {progress && <div className="api-usage-progress">
          <progress className="search-progress" max={100} value={progress.value} aria-label="Monthly EasyCanvas tracked usage" aria-valuetext={`${progress.label} of the monthly warning`} />
          <span>{progress.label}</span>
        </div>}
        {limit === 0 && <p className="assistant-hint">Warning level is $0.00. Any tracked cost reaches this warning.</p>}
        {usage.warning_reached && <p className="usage-warning-notice" role="status">Monthly warning reached. API requests are still allowed.</p>}
        <details className="search-warning-editor" onToggle={event => { if (event.currentTarget.open) event.currentTarget.querySelector("input")?.focus(); }}>
          <summary className="text-link">{limit === null ? "Set monthly warning" : "Edit monthly warning"}</summary>
          <form className="search-budget-form" onSubmit={event => { event.preventDefault(); void save({monthly_warning_usd: warning.trim() ? Number(warning) : null}); }}>
            <label htmlFor="monthly-api-warning">Monthly API usage warning (USD)</label>
            <div><input id="monthly-api-warning" type="number" min="0" max="100000" step="0.01" value={warning} onChange={event => setWarning(event.target.value)} placeholder="No warning set" disabled={busy} />
              <button type="submit" className="primary-button" disabled={busy}>Save warning</button></div>
            <p className="assistant-hint">Leave blank to disable. This warning does not block API requests.</p>
          </form>
        </details>
        <dl className="api-usage-breakdown" aria-label={usage.monthly_breakdown ? "Tracked usage this month" : "Tracked usage since recording began"}>
          {([['Embeddings', breakdown.embedding], ['Vision', breakdown.vision], ['Study Assistant / LLM', breakdown.assistant_other], ['Today', usage.today]] as const).map(([label, total]) =>
            <div key={label}><dt>{label}</dt><dd>{formatApiCost(total.estimated_cost_usd)}</dd></div>)}
        </dl>
        {!usage.monthly_breakdown && <p className="assistant-hint">Feature totals cover all recorded calls; Today covers today only.</p>}
        <dl className="api-usage-counts">
          <div><dt>Requests</dt><dd>{usage.this_month.calls.toLocaleString()} tracked calls</dd></div>
          <div><dt>Input tokens</dt><dd>{usage.this_month.input_tokens.toLocaleString()}</dd></div>
          <div><dt>Output tokens</dt><dd>{usage.this_month.output_tokens.toLocaleString()}</dd></div>
        </dl>
        <div className="api-usage-footnote">
          <p>{began ? `Tracking began ${began}. ${formatApiCost(usage.total.estimated_cost_usd)} tracked since then.` : "Tracking starts with your first recorded API call."}</p>
          {usage.this_month.unpriced_calls > 0 && <p>{usage.this_month.unpriced_calls} calls this month have no complete cost estimate and are excluded from the amount above.</p>}
          <p>Estimated from API calls recorded by EasyCanvas. OpenAI&apos;s billing dashboard is the authoritative account total. Calls made before tracking was enabled or outside EasyCanvas are not included.</p>
        </div>
      </section>}
    </>}
  </section>;
}
