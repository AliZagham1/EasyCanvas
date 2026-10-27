# PRODUCT.md

<!-- impeccable:product-schema 1 -->

## Platform

web

## Product Name

EasyCanvas

## Product Purpose

EasyCanvas is an AI-assisted academic planning and study platform for college students who use Canvas LMS.

Canvas already gives students access to courses, assignments, deadlines, grades, announcements, and course materials. EasyCanvas focuses on the next layer:

> helping students understand what matters, what to work on next, and how to use their available time effectively.

The product should reduce the amount of manual work students do when switching between courses, comparing deadlines, finding relevant lecture material, and deciding how to study.

EasyCanvas is not intended to replace Canvas. It builds on top of Canvas data and course materials to provide better prioritization, planning, and course-aware AI assistance.

---

## Users

Primary users are college students who:

- are enrolled in multiple Canvas courses
- manage several assignments and deadlines at once
- regularly switch between course pages to compare workload
- want help deciding what to work on first
- want AI help that already understands their actual course materials
- want a realistic study plan around classes and other commitments

Initial users are UTA students, but the product should not be designed around one specific course or one specific student.

---

## Operating Context

Students work across multiple Canvas courses whose instructors organize materials differently. The core workflow is to review cross-course unfinished work, enter a course or assignment context, consult authorized course materials, and eventually plan study time around confirmed commitments.

EasyCanvas is a web application with a Next.js frontend and a separate FastAPI backend. Desktop is the primary interface target, with usable narrower layouts, as defined in `DESIGN.md`.

Canvas-native APIs and confirmed student information establish academic facts. Course-content discovery runs separately from essential dashboard loading; unavailable optional Canvas sections must not block the whole application.

---

## Core User Problems

### 1. Coursework is fragmented

Students often have to move between:

- Dashboard
- Assignments
- Modules
- Syllabus
- Pages
- Files
- Announcements
- Discussions

Different instructors organize their Canvas courses differently.

The student may know that an assignment exists but still have to search through several places to find the lecture, instructions, or policy needed to complete it.

---

### 2. Deadlines do not tell the student what matters most

Canvas can show due dates, but students still have to decide:

- what should I start first?
- which task has the highest impact?
- what can wait?
- which assignment is more important even if it has fewer raw points?

EasyCanvas should help prioritize work using understandable factors rather than simply sorting by points.

---

### 3. Generic AI does not automatically know the student's course

A student can ask ChatGPT, Gemini, or another assistant for help, but they often need to manually provide:

- the assignment
- lecture slides
- syllabus
- course context
- deadlines
- grading information

EasyCanvas should already have access to the student's authorized course context through Canvas and its ingestion pipeline.

The goal is not simply to add another chatbot.

The goal is to provide a **course-aware study assistant**.

---

### 4. Study plans often ignore the student's real schedule

A useful study plan must consider when the student is actually available.

EasyCanvas should eventually account for:

- class meeting times
- assignment deadlines
- available study hours
- fixed commitments
- estimated task effort
- assignment priority

The product should not schedule study blocks during known class times.

---

## Core Product Promise

EasyCanvas should help a student answer three questions quickly:

1. **What do I need to do?**
2. **What should I work on first?**
3. **What do I need to know to complete it?**

The product should make academic workload feel more organized, understandable, and manageable.

---

## Core Product Areas

### Dashboard

The Dashboard provides a concise overview of the student's academic workload.

It should help the student quickly understand:

- current courses
- upcoming assignments
- next deadlines
- important work requiring attention

The Dashboard should not duplicate every feature in the application.

---

### Courses

Courses provide a course-specific context.

A course view may include:

- assignments
- upcoming work
- syllabus information
- course materials
- ingestion/sync state
- course-aware AI assistance

When a student enters a course, EasyCanvas should maintain that course context.

---

### Upcoming

Upcoming combines future unfinished work across courses.

It should allow students to see their near-term workload without opening each Canvas course separately.

---

### Priorities

Priorities helps answer:

> What should I work on next?

Ranking should be based on deterministic application logic and should be explainable.

Possible factors include:

- deadline urgency
- submission state
- grading weight or estimated grade impact
- assignment category
- estimated effort
- student availability

Raw assignment points alone should not determine importance.

---

### Class Schedule

Class Schedule stores the student's fixed class meeting times.

Possible sources, in priority order:

1. structured Canvas schedule/calendar information when available
2. syllabus or other course-content extraction
3. manual user entry

Automatically discovered meeting times should be shown to the student for confirmation or correction.

Confirmed class times should become unavailable blocks when generating study plans.

---

### Study Assistant

The Study Assistant is a course-aware AI feature.

The student should be able to choose a course and ask questions such as:

- What should I review before starting this assignment?
- Explain this concept from today's lecture.
- Which lecture covers the material needed for Homework 4?
- What does the syllabus say about late work?
- Summarize this lecture.
- Help me understand this assignment without immediately giving me the final answer.
- What should I study for this quiz?

The assistant should use the student's real course context when available.

It should not behave like a generic chatbot that requires the student to repeatedly upload the same material.

---

### Study Planning

Study Planning should eventually combine:

- upcoming work
- priority
- deadlines
- estimated effort
- confirmed class schedule
- student availability

The result should be an actionable plan the student can review and adjust.

The student should remain in control of the final plan.

---

## Course-Aware AI

Course awareness is a key differentiator for EasyCanvas.

When a student is working inside a selected course, the AI should use that course as the default context boundary.

Example:

```text
Selected course: CSE 3380

Question:
"What concepts do I need for Homework 4?"

EasyCanvas should:
1. retrieve the Homework 4 assignment information
2. search CSE 3380 course materials
3. retrieve relevant lecture or syllabus content
4. provide that context to the language model
5. generate an answer grounded in the course
```

Unrelated courses should not be searched unless the student explicitly requests cross-course help.

---

## Positioning

EasyCanvas should not compete with general AI assistants by trying to be a better generic chatbot.

Its value should come from **automatic academic context and workflow integration**.

A generic AI workflow may require:

```text
Open Canvas
→ find assignment
→ download slides
→ upload slides
→ explain the course
→ provide deadline
→ provide grading context
→ ask for help
```

EasyCanvas should reduce that to:

```text
Open EasyCanvas
→ choose course or assignment
→ ask for help
```

because the relevant authorized course context has already been organized.

The strongest differentiation is the combination of:

- live Canvas academic data
- ingested course materials
- course-aware retrieval
- explainable priority logic
- student schedule awareness
- study planning

---

## Source-of-Truth Product Principle

EasyCanvas must distinguish between factual academic data and AI-generated assistance.

### Structured academic facts

Examples:

- assignment name
- due date
- submission status
- points
- course identity
- confirmed class meeting time

These should come from Canvas or confirmed stored user data.

### Course knowledge

Examples:

- syllabus policies
- lecture concepts
- assignment instructions
- readings

These should come from ingested course documents and retrieval.

### AI output

The AI may:

- explain
- summarize
- reason
- recommend
- help plan
- help interpret course material

The AI must not invent missing academic facts.

---

## Product Trust

Students should be able to understand where important information came from.

When useful, EasyCanvas should identify sources such as:

- Canvas assignment
- course syllabus
- lecture slide deck
- Canvas page
- manually confirmed schedule

The system should avoid presenting uncertain extracted information as guaranteed fact.

When schedule data or other information is automatically extracted, the student should be able to confirm or edit it.

---

## Product Scope

The product should focus on a strong end-to-end academic workflow before expanding into many integrations.

Core scope:

- Canvas course and assignment data
- cross-course upcoming work
- course-content ingestion
- document understanding and retrieval
- course-aware Study Assistant
- priority recommendations
- class schedule awareness
- study-plan generation

Possible later enhancements:

- calendar integrations
- reminders
- additional LMS platforms
- richer agent workflows
- more external integrations

Do not add optional integrations at the cost of completing the core EasyCanvas experience.

---

## Capabilities and Constraints

The product areas and success criteria in this document include planned capabilities; they are not a claim that every feature is implemented. Check the working tree and `AGENTS.md` before treating a capability as available. Current implementation evidence is listed below under **Evidence on Hand**.

### Canvas access varies

Not every Canvas course exposes the same sections or permissions.

EasyCanvas should degrade gracefully when:

- Files is unavailable
- Pages is unavailable
- Quizzes is unavailable
- a course has no syllabus
- course materials are linked only through Modules
- an instructor organizes content differently

The product should use whatever authorized information is actually available.

---

### AI is not always necessary

Do not use an LLM where deterministic logic or direct structured data is more appropriate.

Examples:

- deadline lookup should use assignment data
- submission status should use Canvas data
- priority scoring should use deterministic logic
- semantic course-material questions may use RAG + LLM

---

### Student control matters

EasyCanvas may recommend priorities or schedules, but it should not act as if those recommendations are objectively correct in all situations.

Students should be able to:

- review recommendations
- understand why something was prioritized
- edit schedules
- correct extracted class times
- choose what to work on

---

## Product Tone

EasyCanvas should communicate in a clear, concise, supportive, and professional way.

Avoid:

- exaggerated motivational language
- childish gamification
- overly casual AI language
- unnecessary hype
- vague statements such as "AI-powered magic"

Prefer:

- direct explanations
- specific actions
- clear status messages
- understandable reasons

Example:

Good:

> CSE 3311 is ranked first because the assignment is due tomorrow and is still unsubmitted.

Avoid:

> Your AI-powered productivity journey says it's time to crush CSE 3311! ✨

---

## Visual Product Direction

EasyCanvas should feel like a calm academic productivity tool.

The visual system is defined in `DESIGN.md`.

Product-level expectations:

- prioritize academic information over decoration
- make deadlines easy to scan
- make active course context obvious
- keep the interface cohesive across features
- do not make the Study Assistant look like a generic ChatGPT clone
- avoid generic AI SaaS visual clichés

---

## Product Success Criteria

A successful EasyCanvas experience should allow a student to:

1. connect their Canvas account
2. see their courses and unfinished work
3. understand upcoming deadlines across courses
4. find what deserves attention
5. enter a course and ask questions using that course's real materials
6. understand why the system made a recommendation
7. maintain or confirm class meeting times
8. receive a realistic study plan around fixed commitments
9. use the product without manually rebuilding course context every session

---

## Evidence on Hand

- `frontend/app/page.tsx` and `frontend/app/upcoming/page.tsx` implement the dashboard and cross-course upcoming work using real backend data, with loading and error states.
- `backend/app/canvas_service.py` implements Canvas API access, pagination, course and assignment retrieval, course-content discovery endpoints, individual file metadata, and in-memory file download.
- `backend/app/ingestion_service.py` organizes course-wide discovery and records source availability. `backend/app/file_download_test.py` provides the focused single-file download check.
- The backend ingestion CLI now extracts supported PDF/HTML/text, DOC/DOCX/PPT/PPTX, and Jupyter-notebook content into local SQLite storage, reuses unchanged extractions, and preserves source and page/slide/block/cell metadata. Legacy formats use local conversion and retain quality warnings. `docs/specs/ingestion-pipeline.md` documents its commands and limits; this does not establish complete content coverage for every course.
- All current courses are now synced automatically at backend startup and periodically while it runs. The dashboard Sync Canvas action requests the same backend-owned job and shows its progress; a failed course does not stop other courses. The local implementation uses the configured development Canvas account, not production multi-user OAuth.
- `backend/tests/` contains automated coverage for discovery, normalization, pagination, source failures, and individual file access/download behavior.
- Development has demonstrated that an individual referenced file can be downloadable even when the course-wide Files listing is unavailable. This does not establish access for every file or course.
- `frontend/public/easycanvas-logo.svg` and `frontend/public/easycanvas-logo.png` are the existing brand assets.
- OCR, unsupported document formats, RAG, the AI assistant, deterministic priority scoring, confirmed class schedules, and study-plan generation remain unimplemented. Navigation labels and product descriptions are not evidence that those features are complete.
- User-study results, testimonials, and measured academic or productivity outcomes have not been established here; do not fabricate them.

---

## Product Principles

1. Ground exact academic facts in Canvas or confirmed student data; never invent missing facts.
2. Organize course context once so students do not repeatedly reconstruct it across tools.
3. Use deterministic logic for ranking and structured lookups; use AI for grounded interpretation and assistance.
4. Keep students in control through source transparency, explainable recommendations, and confirmation of extracted schedules.
5. Keep essential coursework usable when optional sources or heavier ingestion are unavailable.

---

## Accessibility & Inclusion

Preserve the keyboard access, visible focus, meaningful labels, readable contrast, and non-color-only state communication required by `DESIGN.md`. Keep narrower layouts usable while prioritizing the desktop workflow. No additional accessibility standard or product-specific assistive-technology requirement was confirmed during initialization.

---

## Current Product Direction

The immediate development priority is to establish the foundation for course-aware intelligence.

That means:

1. reliable Canvas data access
2. course-content ingestion
3. document extraction
4. persistent storage
5. retrieval over course materials
6. a working course-aware Study Assistant
7. first versions of priority and class-schedule features

More advanced orchestration should build on top of these working services rather than replace them.

---

## Final Product Principle

EasyCanvas should not be valuable because it contains AI.

It should be valuable because it understands the student's real academic context and turns that context into useful, explainable actions.
