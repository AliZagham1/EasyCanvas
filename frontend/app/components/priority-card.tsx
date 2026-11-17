import { formatCourseCode } from "@/lib/course-label";
import { formatDueDate, getCourseAccent } from "@/lib/coursework";
import { formatGradeWeight } from "@/lib/priorities";
import type { RankedItem } from "@/lib/priorities";

const LEVEL_LABEL = { high: "High", medium: "Medium", low: "Low" } as const;

// Presentation only: ranking, filtering and fetching happen elsewhere.
export default function PriorityCard({ item }: { item: RankedItem }) {
  const title = item.name || "Untitled assignment";
  const weightKnown = typeof item.grade_weight?.percent === "number";
  return (
    <li className={`priority-card ${getCourseAccent(item.course_id)}`}>
      <div className="priority-card-main">
        <div className="priority-card-heading">
          {item.html_url ? (
            <a className="priority-title" href={item.html_url} target="_blank" rel="noopener noreferrer">
              {title}<span className="sr-only"> (opens in Canvas)</span>
            </a>
          ) : (
            <span className="priority-title">{title}</span>
          )}
          {item.level && <span className={`priority-badge priority-${item.level}`}>{LEVEL_LABEL[item.level]}</span>}
          {item.overdue && <span className="priority-flag">Overdue</span>}
          {item.is_group && <span className="priority-flag">Group</span>}
        </div>
        <p className="priority-course" title={item.course_name ?? undefined}>
          {formatCourseCode(item.course_code, item.course_name || "Course unavailable")}
        </p>
        <p className="priority-explanation">{item.explanation}</p>
        {!item.html_url && <p className="priority-explanation">Canvas link unavailable.</p>}
      </div>
      <dl className="priority-facts">
        <div>
          <dt>Due</dt>
          <dd>{item.due_at && item.dueMs !== null ? <time dateTime={item.due_at}>{formatDueDate(item.due_at)}</time> : "No due date"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{item.statusLabel}</dd>
        </div>
        <div>
          <dt>Estimated grade weight</dt>
          <dd title={weightKnown ? "Estimate from Canvas category weights and points." : item.grade_weight?.reason ?? undefined}>
            {formatGradeWeight(item.grade_weight)}
          </dd>
        </div>
      </dl>
    </li>
  );
}
