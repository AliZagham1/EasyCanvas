# Class Schedule

Weekly class meeting times, with suggestions read from the syllabus and a rule that **only times the student
confirms (or types) are used**. No AI calls and no network requests: the page reads saved data only, so it opens
instantly and never waits for a sync.

## Sources, in priority order (AGENTS.md "Class Schedule Rules")
1. Structured Canvas calendar/meeting data. Checked against a real account: Canvas exposes no recurring meeting times
   (sections carry only term dates; one course had a content timeline, not a class schedule), so this source is not used yet.
2. The syllabus text already ingested (`syllabus_file` and Canvas syllabus documents), read by `backend/app/schedule_service.py`.
3. Manual entry, always available.

## The syllabus reader (rules only)
It looks near headings such as "Time and Place of Class Meetings", "Meeting Time" or "Class Meetings:" (the UTA template
heading) and parses each nearby line conservatively:
- Days: full names ("Tuesday and Thursday"), abbreviations ("Tue & Thu", "Tu/Th", "Th"), and compact codes (MWF, TTh, MoWe).
  A line counts only if everything left after removing the time and room is a day token or a filler word, so ordinary
  sentences ("We will meet ...") are never read as meetings.
- Times: ranges with am/pm ("2:00 pm to 3:20 pm", "11am to 12.20pm", "3:30-4:50 p.m."). A missing am/pm on the start takes the
  end's; "11-12:20 pm" is 11:00 am; a flipped start before 6 am is rejected; a range with no am/pm anywhere is **never guessed**
  (24-hour times are accepted only when unmistakable).
- Rooms ("WH 311", "Location: NH 229"), excluding the course's own department code; Teams/Zoom/online become "Online".
- Skipped: office hours, TA hours, exams, quizzes, deadlines, and a bare value under an "Office Hours" label.
- Several sections in one syllabus: only the student's own section (from the course code, e.g. `-003`) is suggested.
- Days without a time become a **hint** ("Add the time"), never an invented time. Days on one line and a time on another
  (in the same section) combine into one lower-confidence ("check this one") suggestion.
- Every suggestion shows the quoted syllabus line it came from.

Checked against six real syllabi: five yield the correct suggestion (including room and section), and one gives a days-only hint.

## Data
One table, `class_meetings` (per account namespace): confirmed meetings (`source` = `manual` or `syllabus`, with the quoted
line and the originating suggestion's fingerprint) and dismissed suggestions (`status` = `dismissed`). Suggestions themselves
are computed on request and never stored, so they cannot go stale. Times are local wall-clock `HH:MM` in the course's Canvas
time zone (taken from the saved course data).

## API
`GET /api/schedule` (courses, confirmed meetings, suggestions, hints); `POST /api/schedule/meetings`;
`PUT|DELETE /api/schedule/meetings/{id}`; `POST /api/schedule/suggestions/dismiss`;
`DELETE /api/schedule/courses/{id}/dismissed` (restore). Inputs are validated (days, `HH:MM`, end after start, type,
location length, known course); errors are safe messages.

## UI
`/class-schedule`: a weekly grid (Mon-Fri; Sat/Sun only when used; overlapping classes side by side; per-course colors), an
agenda list on phones and for assistive technology, per-course sections with Confirm / Edit first / Not right, an add/edit form,
and a two-step delete. The Dashboard shows a **Next class** card computed on each course's own campus clock from confirmed
meetings only.

## Deadlines on the calendar
The weekly view also shows **unsubmitted deadlines** in a "Due" row under the day names. They come from the same data and rules as
Priorities (`GET /api/priorities`, `buildPriorityView`): only assignments that are incomplete, still submittable, have a due date, and
are **not yet due**. Submitted or graded work, overdue work, excused or locked work, and work with no due date are not shown. A
deadline sits on its local calendar day (Monday to Sunday, in the student's time zone); a day with a deadline but no class (for
example Saturday) is added to the grid. Up to three chips per day, then "+N more" linking to Upcoming. Each chip opens the assignment in
Canvas. The week selector moves forward up to 26 weeks (classes repeat weekly; deadlines are dated); past weeks are not shown because
overdue work is intentionally excluded. On phones the agenda lists them under each day as "Due 11:59 PM", and on wide screens that
list is hidden from assistive technology so the same deadline is not read twice. If Priorities data cannot be loaded, classes still
show with a short note. This is a non-AI feature and needs no AI consent.

## Limitations
- Only recurring weekly meetings. No holidays, exam dates, one-off changes or per-week exceptions.
- "Next class" uses wall-clock minutes in the course's time zone, so it can be an hour off in the week of a daylight-saving change.
- Syllabi that put the schedule in an unusual format (images, tables the extractor flattens, prose) may produce no suggestion;
  the student adds it manually.
- Class time is not yet used by Priorities or study planning; that is the next step.
- Deadlines are shown for the unsubmitted, not-yet-due set only; study blocks are still per course, not tied to an assignment.
