/** Make Canvas course codes scannable while retaining unfamiliar formats. */
export function formatCourseCode(code: string | null | undefined, fallback = "Course unavailable") {
  const match = code?.match(
    /(?:^|[^a-z0-9])([a-z]{2,10})[\s_-]*(\d{3,5}[a-z]?)(?=$|[^a-z0-9])/i
  );
  return match ? `${match[1].toUpperCase()} ${match[2].toUpperCase()}` : code || fallback;
}
