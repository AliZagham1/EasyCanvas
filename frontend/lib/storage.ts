export type StoredCourse = {
  course_id: number; title: string; name: string | null; term_name: string | null;
  term_end: string | null; eligible_after: string | null; kept: boolean;
  eligible: boolean; reason: string | null; materials_removed: boolean;
  counts: Record<string, number>; estimated_bytes: number;
};

export type StoragePreview = {
  preview_id: string; grace_days: number; blocked_reason: string | null;
  courses: StoredCourse[]; eligible_bytes: number; chats_preserved: boolean;
};

const base = "http://127.0.0.1:8000/api/storage";

async function storageRequest(path: string, options?: RequestInit) {
  const response = await fetch(base + path, { cache: "no-store", ...options });
  if (!response.ok) {
    if (response.status === 409) throw new Error("Storage changed. Close this dialog and refresh the preview before trying again.");
    throw new Error("Unable to update course storage. Check the backend and Canvas connection, then retry.");
  }
  return response.json();
}

export async function fetchStorage(signal?: AbortSignal): Promise<StoragePreview> {
  return storageRequest("", { signal });
}

export async function keepCourse(courseId: number, keep: boolean): Promise<void> {
  await storageRequest(`/courses/${courseId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep }),
  });
}

export async function cleanupCourses(courseIds: number[], previewId: string): Promise<{compacted: boolean}> {
  return storageRequest("/cleanup", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ course_ids: courseIds, preview_id: previewId }),
  });
}

export function formatStorageBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
