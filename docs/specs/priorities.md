# Priorities

Answers "what is due, when, and what should I do first?" for every current Canvas course. Deterministic:
no LLM, no paid calls, no new tables, no grade calculations. Read-only against Canvas.

## Data flow

`GET /api/priorities[?refresh=true]` (`backend/app/priority_service.py`) makes one Canvas request per current course
(the assignment listing with `include[]=submission`, so `due_at` is the student-effective date) plus the course list, and
returns normalized facts and `failed_course_ids`. Classification, sorting and filtering run in the browser
(`frontend/lib/priorities.ts`) against the current time and the viewer's time zone.

Cache: per course (and course list) for 60 seconds. `refresh=true` bypasses it; the page sends it after a **Sync Canvas**
run completes and from the **Refresh** button. Failures are never cached and a failed refresh evicts the old entry; a fetch
that started earlier cannot overwrite a later one. A failing course is reported, not hidden, and does not block others.

## Sections and rules

Only deadline and reliable Canvas status are used.

| Section | Rule |
|---|---|
| Overdue | incomplete, still submittable, due date passed. Labeled High. Most recently overdue first (first 5 shown, rest behind "Show all"). |
| Upcoming | incomplete, submittable, dated, sorted by earliest deadline. **High** due within 24 h, **Medium** within 72 h, **Low** later. |
| No due date | missing or unparsable due date. No urgency is invented. |
| Not actionable | excused, not yet unlocked, past `lock_at`, or locked for the student. Never recommended as work. |
| Completed | Canvas `submitted`/`pending_review`, or `graded` (unless flagged missing with no submission). Hidden by default. |

On-paper, `none`, `not_graded` and external-tool assignments that show "unsubmitted" stay in the lists and are labeled
"Not tracked online"; they are never marked completed. A missing submission record shows "Status unavailable". Canvas'
`missing` flag shows "Missing". Optional or extra-credit status is not guessed.

Summary tiles are mutually exclusive (Overdue, due within 24 hours, due in 1-3 days, Completed), so nothing is counted
twice. "Due tomorrow" and day counts follow the viewer's local calendar. Filters: course, priority (overdue counts as
High), show completed. Sort: due date or course.

## Limitations

- Completion depends on what Canvas records; paper and external-tool work cannot be confirmed.
- Late penalties, grading periods and per-student override edge cases are not modeled.
- Effort is not estimated and there are no manual completion states.
