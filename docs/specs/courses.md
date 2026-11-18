# Courses

A course home page: pick one course and see everything EasyCanvas knows about it. It reads **saved data only** (no Canvas
request, no AI, no waiting for a sync), plus the live Priorities data for assignment status.

## Courses page (`/courses`)
One card per saved current course: code and cleaned title, **next class** (from confirmed class times only, on the course's own
time zone), a one-line workload summary ("1 overdue", "1 due within 24 hours", "Nothing due"), counts of modules, files and
announcements, and the Study Assistant search state. The Dashboard's "View ->" and the sidebar "Courses" link lead here.

## Course page (`/courses/[courseId]`)
- **Header**: code, title, last sync time, search readiness; buttons for **Ask the Study Assistant** (`/study-assistant?course=ID`,
  which opens that course if it is one of the loaded courses), **Open in Canvas**, and **Syllabus** (Canvas's syllabus page).
- **Next class** card and four workload tiles (Overdue, due within 24 hours, due in 1-3 days, Completed).
- **Assignments**: this course's work with the same classification and rows as Priorities (overdue, upcoming, no due date;
  completed/closed behind a checkbox).
- **Class times**: confirmed meetings with a link to Class Schedule.
- **Announcements**: newest first as plain text (HTML removed), linked to Canvas; the latest 15 are saved.
- **Materials**: modules with their saved files and pages, each opening in Canvas.

## Data
- `GET /api/course-catalog` and `GET /api/courses/{id}/details` (`backend/app/course_overview_service.py`) read the saved course
  snapshot: announcements (HTML stripped to a 240-character excerpt), modules (Canvas order; sub-headers and unpublished items
  left out), counts, confirmed meetings, and Canvas links built from the configured Canvas URL.
- Assignment status comes from `GET /api/priorities` (60-second cache) and the same ranking code as the Priorities page, so the
  two pages can never disagree. If it fails, the page still loads and says "unavailable" instead of showing zeros.
- No new tables. `/courses/[courseId]` shows "not found" for an unknown or non-numeric id, which is distinct from a backend error.

## Limitations
- Modules list only the files and pages EasyCanvas saved; assignments, quizzes and headers inside a module are not listed there.
- Announcements are the saved ones, not live; Sync Canvas refreshes them.
- The syllabus is a link to Canvas. Pulling facts out of it (office hours, instructor email, AI-use rule) is a possible next step.
- No grades or grade estimates.
