# Smart Priorities

Answers "what should I work on first, and why?" for every current Canvas course. Deterministic: no LLM,
no paid calls, no new tables. Read-only against Canvas.

## Data flow

`GET /api/priorities[?refresh=true]` (`backend/app/priority_service.py`) fetches, per current course,
assignments (`include[]=submission,all_dates`, so `due_at` is the student-effective date), assignment groups, and
course settings (`apply_assignment_group_weights`, `hide_final_grades`). It returns normalized facts plus
`failed_course_ids`. Ranking and filtering run in the browser (`frontend/lib/priorities.ts`) against the current time
and the viewer's time zone.

Cache: per course (and course list) for 60 seconds. `refresh=true` bypasses it; the page sends it after a **Sync Canvas**
run completes and from the **Refresh** button. Failures are never cached and a failed refresh evicts the old entry; a
fetch that started earlier cannot overwrite a later one. A failing course is reported, not hidden, and does not
block other courses. Missing group/settings data only makes the grade weight unavailable.

## Sections

1. **Completed**: Canvas `submitted`/`pending_review`, or `graded` (unless flagged missing with no submission).
2. **Not actionable**: excused, not yet unlocked, past `lock_at`, or `locked_for_user`. Never recommended as work.
3. **No due date** (or unparsable): listed separately; no urgency invented.
4. **Active**: everything else, ranked below.

On-paper, `none`, `not_graded` and external-tool assignments that show "unsubmitted" are ranked but labeled
"Not tracked online — submission unverified"; they are never marked completed. A missing submission record shows
"Submission status unavailable".

## Ranking

Priority label (High/Medium/Low) is separate from order.

| Tier | Condition | Label |
|---|---|---|
| 0 | overdue, still open (most recently overdue first) | High |
| 1 | due within 24 h | High |
| 2 | due within 72 h | Medium |
| 3 | later | Low |

Within tiers 1-3: earlier **local calendar day** first (viewer's time zone). Within one day, work due within
`IMMINENT_HOURS = 3` of now stays first in due order. The remaining items are ordered by due time; then the slots
held by items that have an estimated grade weight are refilled in descending weight. Items without an estimate never
move and are never treated as 0%. Weight never reorders across days or tiers. Ties: due time, course id, assignment id.
Each card explains its label, and notes when weight changed the order.

## Estimated grade weight

Shown as "Estimated grade weight (~X%)", otherwise "Unavailable" with a reason. Computed only when all hold:
course does not hide final grades; assignment is published, graded, counts toward the final grade, and has positive
points; no assignment group has drop rules; no zero-point (possible extra-credit) work in scope; for weighted courses,
group weights sum to 100 and every weighted group has graded work. Weighted: `weight x points / group points`.
Unweighted: `points / all counting points`. Excused work leaves the totals. It is an estimate of the final-grade share,
not a grade forecast, and is never compared across courses.

## Limitations

- Estimates are unavailable for many courses (hidden final grades, weights not totaling 100, empty categories).
- Completion depends on Canvas; paper/external work cannot be confirmed.
- Grading periods, late penalties and per-student override edge cases are not modeled.
- Effort is not estimated; there are no manual completion states.
