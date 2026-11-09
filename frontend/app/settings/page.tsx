"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cleanupCourses, fetchStorage, formatStorageBytes, keepCourse } from "@/lib/storage";
import type { StoragePreview } from "@/lib/storage";
import SearchSettings from "../components/search-settings";

export default function SettingsPage() {
  const [preview, setPreview] = useState<StoragePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [confirming, setConfirming] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const chosen = preview?.courses.filter(course => course.eligible && selected.includes(course.course_id)) || [];
  const selectedBytes = chosen.reduce((total, course) => total + course.estimated_bytes, 0);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await fetchStorage(signal);
      if (!signal?.aborted) { setPreview(data); setSelected([]); setError(""); }
    } catch {
      if (!signal?.aborted) {
        setPreview(null);
        setError("Unable to load the storage preview. Check the backend and Canvas connection, then retry.");
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchStorage(controller.signal).then(data => {
      if (!controller.signal.aborted) { setPreview(data); setSelected([]); }
    }).catch(() => {
      if (!controller.signal.aborted) setError("Unable to load the storage preview. Check the backend and Canvas connection, then retry.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (confirming) { dialog.current?.showModal(); cancel.current?.focus(); }
    else dialog.current?.close();
  }, [confirming]);

  async function keep(courseId: number, value: boolean) {
    const previous = preview;
    setPending(true); setError(""); setMessage("");
    setPreview(current => current && { ...current, courses: current.courses.map(course => course.course_id === courseId
      ? { ...course, kept: value, eligible: value ? false : course.eligible } : course) });
    setSelected(current => current.filter(id => id !== courseId));
    try { await keepCourse(courseId, value); await load(); }
    catch { setPreview(previous); setError("Your retention choice could not be saved. Please retry."); }
    finally { setPending(false); }
  }

  async function remove() {
    if (!preview || !chosen.length || pending) return;
    setPending(true); setError("");
    try {
      const result = await cleanupCourses(chosen.map(course => course.course_id), preview.preview_id);
      setConfirming(false);
      setMessage(result.compacted ? "Selected course materials deleted. Saved conversations were kept."
        : "Selected course materials deleted. Saved conversations were kept. The database is busy; freed space can be reused by new courses.");
      setLoading(true); await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cleanup could not be completed. Refresh the preview and retry.");
    } finally { setPending(false); }
  }

  return <div className="page-content storage-page">
    <header className="page-header"><div>
      <h1 className="page-title">Settings</h1>
      <p className="page-description">Manage course search, API usage, and the materials saved on this computer.</p>
    </div></header>
    <SearchSettings />
    <section aria-labelledby="storage-title">
      <div className="storage-heading">
        <div><h2 id="storage-title" className="section-title">Course storage</h2>
          <p className="assistant-hint">Previous courses become eligible for cleanup 30 days after their semester ends. Nothing is deleted automatically.</p></div>
        <button type="button" className="primary-button" disabled={loading || pending}
          onClick={() => { setLoading(true); setError(""); void load(); }}>Refresh preview</button>
      </div>
      <p className="storage-explanation">Keep any course you may need again. Deleting its materials removes saved documents, extracted text, chunks, embeddings, and visual extractions from this computer. Saved conversations and Canvas files are kept.</p>
      {loading && <p role="status" className="coverage-notice">Checking saved courses and current semester...</p>}
      {message && <p role="status" className="coverage-notice">{message}</p>}
      {error && !confirming && <p role="alert" className="coverage-notice">{error}</p>}
      {!loading && preview?.blocked_reason && <p className="coverage-notice" role="status">{preview.blocked_reason}</p>}
      {!loading && preview && (preview.courses.length ? <div className="coursework-panel">
        <table className="coursework-table storage-table" role="table">
          <caption className="sr-only">Saved course materials and cleanup eligibility</caption>
          <thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Course</th><th scope="col" role="columnheader">Saved materials</th><th scope="col" role="columnheader">Retention</th></tr></thead>
          <tbody role="rowgroup">{preview.courses.map(course => <tr key={course.course_id} role="row">
            <td data-label="Course" role="cell"><div><label className="storage-selection">
              <input type="checkbox" disabled={!course.eligible || pending} aria-label={`Select ${course.title} for cleanup`}
                checked={selected.includes(course.course_id)} onChange={event => setSelected(previous => event.target.checked
                  ? [...previous, course.course_id] : previous.filter(id => id !== course.course_id))} />
              <span>{course.title}</span></label>
              <p className="assistant-hint">{course.term_name || "Semester dates unavailable"}</p></div></td>
            <td data-label="Saved materials" role="cell"><div>{course.materials_removed ? "Removed" : formatStorageBytes(course.estimated_bytes)}
              {!course.materials_removed && <p className="assistant-hint">{course.counts.documents || 0} documents</p>}</div></td>
            <td data-label="Retention" role="cell"><div><label className="storage-selection">
              <input type="checkbox" checked={course.kept} disabled={pending || course.materials_removed}
                aria-label={`Keep ${course.title} materials`} onChange={event => void keep(course.course_id, event.target.checked)} />Keep materials</label>
              <p className="assistant-hint">{course.eligible ? "Eligible for cleanup" : course.reason}</p></div></td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="coverage-notice">No course materials have been saved yet. Sync Canvas to get started.</p>)}
      {preview && !loading && <div className="storage-footer"><p className="assistant-hint">
        {chosen.length} {chosen.length === 1 ? "course" : "courses"} selected · {formatStorageBytes(selectedBytes)} estimated data<br />Actual disk space reclaimed may differ because of database overhead.
      </p><button type="button" className="primary-button conversation-action-danger" disabled={!chosen.length || pending}
        onClick={() => { setError(""); setConfirming(true); }}>Delete selected materials</button></div>}
    </section>
    <dialog ref={dialog} className="conversation-dialog" aria-labelledby="cleanup-title" aria-describedby="cleanup-description"
      onCancel={event => { event.preventDefault(); if (!pending) setConfirming(false); }}
      onKeyDown={event => {
        if (event.key !== "Tab") return;
        const controls = event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
        const first = controls[0], last = controls[controls.length - 1];
        if (!first) event.preventDefault();
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }}>
      <h2 id="cleanup-title">Delete selected course materials?</h2>
      <p id="cleanup-description" className="conversation-delete-description">This permanently removes approximately {formatStorageBytes(selectedBytes)} of saved material for {chosen.map(course => course.title).join(", ")}. Course materials may be difficult to recover after Canvas access ends. Saved conversations and Canvas files will not be deleted.</p>
      {error && <p role="alert" className="conversation-dialog-error">{error}</p>}
      {pending && <p role="status" className="assistant-hint">Deleting materials and reclaiming database space...</p>}
      <div className="conversation-dialog-footer">
        <button ref={cancel} type="button" className="primary-button" disabled={pending} onClick={() => setConfirming(false)}>Cancel</button>
        <button type="button" className="primary-button conversation-delete-button" disabled={pending} onClick={() => void remove()}>Delete materials</button>
      </div>
    </dialog>
  </div>;
}
