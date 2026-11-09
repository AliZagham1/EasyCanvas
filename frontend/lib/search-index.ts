export type DocumentIndex = {
  document_key: string; title: string | null; eligible_chunks: number; indexed_chunks: number;
  pending_chunks: number; failed_chunks: number; remaining_chunks: number; extraction_status: string;
};
export type CourseIndex = {
  course_id: number; title: string; eligible_chunks: number; indexed_chunks: number;
  pending_chunks: number; failed_chunks: number; remaining_chunks: number;
  status: "empty" | "ready" | "updating" | "pending" | "failed" | "syncing";
  automatic: boolean; last_successful_at: string | null; documents: DocumentIndex[];
};
export type UsageTotal = { estimated_cost_usd: number; calls: number; unpriced_calls: number; input_tokens: number; output_tokens: number };
export type SearchSettings = { auto_index: boolean; monthly_warning_usd: number | null };
export type SearchPreferences = { settings: SearchSettings; usage: {
  today: UsageTotal; this_month: UsageTotal; total: UsageTotal; embedding: UsageTotal;
  vision: UsageTotal; assistant_other: UsageTotal; warning_reached: boolean;
  monthly_breakdown?: {embedding: UsageTotal; vision: UsageTotal; assistant_other: UsageTotal};
  recording_started_at: string | null; notice: string;
} };
const base = "http://127.0.0.1:8000/api";
async function request(path: string, options?: RequestInit) {
  const response = await fetch(base + path, { cache: "no-store", ...options });
  if (!response.ok) throw new Error("Course search could not be updated. Check the backend and retry.");
  return response.json();
}
export function getSearchStatus(courseId: number, signal?: AbortSignal): Promise<CourseIndex> {
  return request(`/courses/${courseId}/search-index`, { signal });
}
export function updateSearch(courseId: number): Promise<CourseIndex> {
  return request(`/courses/${courseId}/search-index`, { method: "POST" });
}
export function getSearchCourses(signal?: AbortSignal): Promise<{courses: CourseIndex[]}> {
  return request("/search/status", { signal });
}
export function getSearchSettings(signal?: AbortSignal): Promise<SearchPreferences> {
  return request("/search/settings", { signal });
}
export function saveSearchSettings(settings: Partial<SearchSettings>): Promise<SearchPreferences> {
  return request("/search/settings", { method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify(settings) });
}
export function searchStatusLabel(status: CourseIndex) {
  if (status.status === "syncing") return "Syncing course material. Search coverage will update when sync finishes.";
  if (status.status === "ready") return "Study Assistant ready";
  if (status.status === "empty") return "No searchable material saved yet. Sync Canvas to get started.";
  if (status.status === "updating") return `Updating course search — ${status.remaining_chunks} passages remaining`;
  return `Some course material is not searchable yet — ${status.remaining_chunks} passages remaining`;
}
export function formatApiCost(amount: number) {
  if (amount > 0 && amount < 0.0001) return "<$0.0001";
  return `$${amount.toFixed(amount > 0 && amount < 0.01 ? 4 : 2)}`;
}

/** The bar stops at full; the label still reports usage above the warning. */
export function usageWarningProgress(amount: number, warning: number | null) {
  if (warning === null || warning <= 0) return null;
  const percent = Math.max(0, amount / warning * 100);
  return { value: Math.min(percent, 100), label: percent > 0 && percent < 0.1 ? "<0.1%" : `${percent.toFixed(1)}%` };
}

export function compactSearchStatus(course: CourseIndex) {
  if (course.status === "ready") return "Ready";
  if (course.status === "updating") return "Updating…";
  if (course.status === "syncing") return "Syncing…";
  if (course.status === "failed") return "Indexing failed";
  if (course.status === "empty") return "No material yet";
  return `${course.remaining_chunks.toLocaleString()} passages pending`;
}
