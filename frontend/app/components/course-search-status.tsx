"use client";
import { useEffect, useState } from "react";
import { getSearchStatus, searchStatusLabel, updateSearch } from "@/lib/search-index";
import type { CourseIndex } from "@/lib/search-index";

export default function CourseSearchStatus({ courseId }: { courseId: number }) {
  const [status, setStatus] = useState<CourseIndex | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const data = await getSearchStatus(courseId, controller.signal);
        if (!controller.signal.aborted) { setStatus(data); setError(""); }
      } catch {
        if (!controller.signal.aborted) setError("Course search status is unavailable. Check the backend and retry.");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 10000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [courseId]);
  async function update() {
    if (busy) return;
    setBusy(true); setError("");
    try { setStatus(await updateSearch(courseId)); }
    catch { setError("Course search could not be updated. Please retry."); }
    finally { setBusy(false); }
  }
  return <div className="course-search-status">
    <div><p role="status">{status ? searchStatusLabel(status) : error ? "Course search status unavailable." : "Checking course search..."}</p>
      {status && status.eligible_chunks > 0 && <p className="assistant-hint">{status.indexed_chunks.toLocaleString()} / {status.eligible_chunks.toLocaleString()} passages searchable</p>}
      {error && <p className="assistant-hint" role="status">{error}</p>}</div>
    {(!status || status.remaining_chunks > 0 || !status.automatic) && <button type="button" className="primary-button"
      disabled={busy || status?.status === "updating"} onClick={() => void update()}>
      {busy ? "Queuing update..." : status?.status === "failed" ? "Retry indexing" : "Update course search"}</button>}
  </div>;
}
