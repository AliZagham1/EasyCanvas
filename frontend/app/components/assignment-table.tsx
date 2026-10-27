import { formatCourseCode } from "@/lib/course-label";
import { formatDueDate } from "@/lib/coursework";
import type { Assignment } from "@/lib/coursework";

type AssignmentTableProps = {
  assignments: Assignment[];
  caption: string;
  showPoints?: boolean;
};

// Presentation only: pages own fetching, status messages, and the preview limit.
export default function AssignmentTable({ assignments, caption, showPoints = false }: AssignmentTableProps) {
  return (
    <table className="coursework-table assignments-table" role="table">
      <caption className="sr-only">{caption}</caption>
      <colgroup>
        <col className="assignment-course-column" />
        <col className="assignment-name-column" />
        <col className="assignment-date-column" />
      </colgroup>
      <thead role="rowgroup">
        <tr role="row">
          <th scope="col" role="columnheader">Course</th>
          <th scope="col" role="columnheader">Assignment</th>
          <th scope="col" role="columnheader">Due Date</th>
        </tr>
      </thead>
      <tbody role="rowgroup">
        {assignments.map((assignment) => (
          <tr key={`${assignment.course_id}-${assignment.id}`} role="row">
            <td data-label="Course" role="cell">
              <div className="course-code" title={assignment.course_code}>
                {formatCourseCode(assignment.course_code)}
              </div>
            </td>
            <td data-label="Assignment" role="cell">
              <div>
                <span className="assignment-title">{assignment.name}</span>
                {showPoints && (
                  <span className="cell-secondary">
                    {assignment.points_possible == null ? "Points unavailable" : `${assignment.points_possible} points`}
                  </span>
                )}
              </div>
            </td>
            <td data-label="Due Date" role="cell">
              <div>
                <time dateTime={assignment.due_at} className="assignment-deadline">
                  {formatDueDate(assignment.due_at)}
                </time>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
