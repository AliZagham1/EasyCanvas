import { formatCourseCode } from "@/lib/course-label";
import { formatDueDate, formatDueShort } from "@/lib/coursework";
import type { RankedItem } from "@/lib/priorities";

const STATUS_HINT = {
  "Not tracked online": "Canvas cannot confirm whether this was submitted.",
  "Status unavailable": "Canvas did not return submission information.",
} as Record<string, string>;

// Presentation only: classification, sorting and fetching happen elsewhere.
// Overdue and urgency are shown by the section, the due text and (for High/Medium) a plain word, never by a colored badge or stripe.
export default function PriorityCard({ item, showCourse = true }: { item: RankedItem; showCourse?: boolean }) {
  const title = item.name || "Untitled assignment";
  const level = item.overdue ? null : item.level;
  return (
    <li className={`priority-row work-row${item.overdue ? " is-overdue" : ""}`}>
      <div className="priority-main">
        <div className="priority-line">
          {item.html_url ? (
            <a className="priority-title" href={item.html_url} target="_blank" rel="noopener noreferrer">
              {title}<span className="sr-only"> (opens in Canvas)</span>
            </a>
          ) : (
            <span className="priority-title" title="Canvas link unavailable">{title}</span>
          )}
        </div>
        <p className="priority-meta">
          {showCourse && <><span title={item.course_name ?? undefined}>{formatCourseCode(item.course_code, item.course_name || "Course unavailable")}</span>
          <span aria-hidden="true"> · </span></>}
          <span className={item.statusLabel === "Missing" ? "priority-missing" : undefined} title={STATUS_HINT[item.statusLabel]}>
            {item.statusLabel}
          </span>
          {item.is_group && <span> · Group</span>}
          {level === "high" && <span className="priority-level"> · High priority</span>}
          {level === "medium" && <span className="priority-level"> · Medium priority</span>}
          {level === "low" && <span className="sr-only"> · Low priority</span>}
        </p>
      </div>
      {(item.due_at || item.dueLabel) && (
        <div className="priority-due">
          {item.due_at && item.dueMs !== null && <time dateTime={item.due_at} title={formatDueDate(item.due_at)}>{formatDueShort(item.due_at)}</time>}
          {item.dueLabel && <span className="priority-relative">{item.dueLabel}</span>}
        </div>
      )}
    </li>
  );
}
