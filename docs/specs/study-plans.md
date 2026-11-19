# Study plans (Stage A: no AI)

A student's own plan for an assignment or an exam: a list of steps, and times for those steps found automatically in their free
hours. Nothing in this feature calls an AI service, so it works whether or not the student agreed to AI consent.

## Rules
- **Deadlines are never guessed.** An assignment plan takes its title and due date from Canvas (the same data and 60-second cache
  as Priorities, `GET /api/priorities`); whatever the client sends for those is ignored. Submitted, graded, excused, undated and
  already-past assignments cannot be planned. An exam or other item ("custom") is named and dated by the student; the date must
  include a time zone and be in the future.
- **Free time is what is left.** From now until the deadline minus a buffer, inside the student's daily study hours, after
  removing every confirmed class meeting and study block (the weekly schedule, read on the course's own clock) and every saved
  session of any plan. Gaps under 30 minutes are not offered. Day boundaries follow the course's time zone, so daylight-saving
  changes keep the same wall-clock hours.
- **Placement is first-fit and honest.** Steps are placed in order into the earliest gap that holds them, with a 10-minute break
  between steps, never above the daily limit (saved sessions count toward it). A step that does not fit is reported with the
  reason ("no free gap of N minutes" or "daily study limit") and the minutes needed against the minutes free. Nothing is squeezed in.
- **Times are only proposed.** `POST .../suggest` computes and saves nothing. `PUT .../times` saves what the student accepts, after
  the server re-checks each session: exact length, not in the past, finishes before the deadline, one day, no overlap with a class,
  another session, or another accepted session. A refusal saves nothing.
- Changing a step's length clears its saved time; marking a step done removes it from scheduling and from the calendar.

## Choosing a time yourself
Each open step has **Pick time** (or **Change time**): the student chooses a date and start time, and the end is the step's length. The
time is saved with `PUT /api/study-plans/{id}/activities/{step}/time` and shows on the Class Schedule week as a study block.
- If the time overlaps something already booked, the server answers **409** with a list of what it overlaps (classes, recurring study
  blocks, and other plans' sessions), and nothing is saved. The student can **Choose another time** or **Add anyway**
  (`replace_conflicts: true`).
- **Add anyway** keeps classes and recurring study blocks where they are (they are weekly commitments, so both show on that day) and
  **replaces any other study session** at that time: that session loses its time and shows as "Not scheduled". The page says exactly
  what was replaced and what it overlaps.
- Adding anyway never overrides the hard rules: the step must not be finished, the time must not have passed, the session must end
  before the deadline and stay within one day. Those are errors with no "add anyway".
- Moving a step over its own old time is not a conflict.

## Preferences (stored in app settings)
Study hours (default 09:00-21:00), most study per day (240 min), and how long before the deadline to finish (120 min). Validated on
the server; a corrupt saved value falls back to the defaults.

## Data
Tables `study_plans` and `plan_activities` in the local SQLite database. They are the student's own data, so "Delete course
materials" in Settings does not remove them.

## API
`GET /api/study-plans`; `POST /api/study-plans`; `DELETE /api/study-plans/{id}`; `PUT /api/study-plans/preferences`;
`POST /api/study-plans/{id}/activities`; `PATCH|DELETE /api/study-plans/{id}/activities/{step}`;
`POST /api/study-plans/{id}/suggest`; `PUT /api/study-plans/{id}/times`; `PUT /api/study-plans/{id}/activities/{step}/time` (409 + `conflicts` when booked); `DELETE /api/study-plans/{id}/activities/{step}/time`.
Errors are safe messages (422 for rules, 404 for unknown items, 503 when Canvas or storage is unavailable).

## UI
`/study-plans` (sidebar: Study Plans): new plan (assignment or exam), steps with a length, Suggest times, review and Save, mark done,
clear a time, delete. Saved, unfinished sessions appear as study blocks on the weekly Class Schedule grid for their date, and exam or other custom dates appear in its "Due" row, on the Dashboard's Upcoming list and on the Upcoming page, each with an Exam tag.

## Limitations
- No AI yet: the student writes the steps. The planned next stage is AI-suggested steps from the assignment and course material,
  which would need AI consent and would not touch dates or times.
- A step is one session in one gap (at most 4 hours); it is not split across days.
- Steps cannot be reordered. A session is placed at one start time; it cannot be split.
- Free time ignores everything except the weekly schedule and saved sessions (no holidays, work or personal events).
- Exams are not read from Canvas; the student enters the date. They appear on the Class Schedule week, the Dashboard, Upcoming and the matching course page (with an Exam tag, linking to Study Plans), but not on Priorities, which ranks Canvas work by submission status that exams do not have.
