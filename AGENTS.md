# AGENTS.md

## Project Overview

EasyCanvas is an AI-assisted academic planning platform for students who use Canvas LMS.

The current application connects to Canvas, retrieves the student's current courses and assignments, and presents upcoming coursework in a simpler cross-course dashboard.

The long-term goal is to make EasyCanvas a course-aware study assistant that can understand course materials, help students decide what to work on, answer questions using their real course content, and generate realistic study plans.

The system must treat Canvas and confirmed user data as the source of truth. AI should help interpret, explain, search, and plan, but it must not invent course facts such as assignment names, deadlines, submission status, class meeting times, or grading weights.

---

## Current Technology Stack

### Frontend
- Next.js
- TypeScript
- Tailwind CSS
- App Router

### Backend
- Python
- FastAPI
- Requests
- Pydantic

### Planned Data Layer
- PostgreSQL
- pgvector or another vector store for document embeddings

### Planned AI Layer
- Retrieval-Augmented Generation (RAG)
- Course-aware LLM-based study assistant
- A separate embedding model for vector search over course materials
- MCP tools and LangGraph are planned components of the EasyCanvas architecture. They should be introduced only after the backend services they depend on are implemented and stable.

---

## Current Working Features

The following behavior is currently implemented in the committed repository:

1. Canvas authentication using environment variables.
2. Retrieval of Canvas courses.
3. Retrieval of assignments for a selected course.
4. Inclusion of Canvas submission information with assignments.
5. Normalization of assignment fields used by EasyCanvas.
6. Filtering of upcoming assignments to include only future, unsubmitted work.
7. Sorting assignments by due date.
8. A simple development script for testing assignment retrieval against Canvas.

Do not assume that course-content ingestion, file processing, RAG, priority scoring, study-plan generation, class-schedule extraction, or AI-agent functionality is implemented unless the relevant code exists in the current working tree.

The current working tree also provides course-wide ingestion: native
course discovery, unique file-reference discovery, supported PDF/HTML/text,
DOC/DOCX/PPT/PPTX, and Jupyter-notebook extraction, and incremental local SQLite storage. See
`docs/specs/ingestion-pipeline.md` for commands, service responsibilities, verified behavior,
and remaining coverage limits. SQLite is the development store; PostgreSQL is
still planned. The backend automatically syncs all current courses on startup
and every 30 minutes, with persistent progress and a dashboard Sync Canvas action.
It reuses the dashboard's current-course selection and keeps ingestion in a
separate worker process. Legacy DOC/PPT use a local LibreOffice converter; converted Word
structure remains explicitly unverified. OCR, other document formats, RAG, and AI
assistant functionality are not implemented by this ingestion step.

## Current Development Priorities

Work should currently focus on these areas, in roughly this order:

1. Reliable course-content ingestion.
2. Incremental syncing and local persistence.
3. Document extraction and normalization.
4. Class schedule discovery and confirmation.
5. Explainable priority calculation.
6. RAG over course documents.
7. AI study assistant.
8. Study-plan generation.
9. MCP and LangGraph only after the underlying services are stable.

Do not skip foundational ingestion and data-quality work just to add AI features sooner.


## Planned Features / Current Development Direction

The following are planned and should not be treated as complete unless verified in the current working tree:

- discover syllabus content
- discover modules and module items
- discover linked Canvas files
- process course documents
- extract and normalize document text
- incremental course-content syncing
- local persistence of normalized course data
- class schedule discovery and confirmation
- deterministic priority engine
- RAG over course documents
- course-aware AI Study Assistant for assignment help, lecture/slides questions, syllabus questions, and study guidance
- study-plan generation
- MCP tools and LangGraph orchestration at a later stage

---

- Canvas course-content discovery for syllabus, assignments, modules, module items, pages when available, files when available, quizzes when available, announcements, and discussions.
- Discovery and download of individual Canvas files referenced by module items, including cases where the course-wide Files endpoint is unavailable.
## Architecture Responsibilities

Keep responsibilities separated.

### `canvas_service.py`

Owns communication with the Canvas API.

Responsibilities include authentication headers, pagination, retrieving courses and course content, retrieving individual file metadata, and downloading individual Canvas files.

Do not put RAG, scheduling, priority scoring, or UI logic in this file.

### `ingestion_service.py`

Owns discovery and synchronization of course content.

Responsibilities include discovering Canvas-native content, normalizing Canvas records, tracking source availability, comparing remote metadata with stored metadata, deciding which content is new or changed, and passing downloadable content to the appropriate document-processing service.

This service should eventually support incremental sync instead of redownloading everything on every application load.

### `document_service.py`

Owns document processing.

Responsibilities should include extracting text from supported files, normalizing extracted text, preserving useful document metadata, and preparing content for chunking and later RAG.

Document parsing should be separate from Canvas networking.

### Priority service

A dedicated priority service should eventually own deterministic assignment ranking.

Priority must not be decided directly by an LLM.

Expected signals include deadline urgency, grading weight or estimated grade impact, submission state, estimated effort, and user availability.

The output should be explainable.

### Schedule service

A dedicated schedule service should eventually own class meeting times and fixed schedule blocks.

Possible schedule sources, in priority order:

1. Structured Canvas calendar or meeting data when available.
2. Syllabus or other course documents.
3. Other Canvas course content.
4. Manual user entry.

Automatically extracted schedules must be shown to the user for confirmation before they become trusted planning data.

### RAG / Study Assistant

RAG is for course-document knowledge such as syllabus policies, lecture content, assignment instructions, course notes, and readings.

Do not use vector search as the source of truth for exact structured facts that are already available directly from Canvas or the database.

Examples of structured facts include assignment deadline, submission status, course ID, assignment points, and confirmed class meeting time.

---

## AI Model Strategy

EasyCanvas should separate conversational reasoning from semantic retrieval.

### Main LLM

The current recommended primary model for the Study Assistant is OpenAI GPT-5.6 Sol.

Use the main LLM for:
- explaining course concepts
- answering assignment questions
- summarizing retrieved material
- generating study recommendations
- reasoning over structured data and retrieved course context
- later tool-using and multi-step workflows

Keep model-provider calls behind a dedicated service boundary such as `llm_service.py` so the provider or model can be changed later without rewriting the application.

### Embedding Model

Use a separate embedding model for RAG.

The embedding model does not answer student questions. Its job is to convert course text and user questions into vectors so EasyCanvas can find semantically relevant material.

Expected flow:

1. Ingest course documents.
2. Extract and chunk text.
3. Create embeddings for the chunks.
4. Store vectors with course and document metadata.
5. Embed the student's question.
6. Search only the relevant course's vectors.
7. Send the best matching chunks to the main LLM.
8. Generate the answer from that grounded context.

Do not use the main reasoning model as a replacement for the embedding/search layer.

## Source-of-Truth Rules

Use the most reliable source available.

### Canvas is the source of truth for:
- course identity
- assignment identity
- assignment deadline
- assignment points
- submission status
- Canvas-hosted metadata

### Confirmed user data is the source of truth for:
- manually entered class times
- availability
- work schedule
- preferred study hours
- corrections to automatically extracted schedule information

### Course documents may provide:
- grading policies
- assignment-category weights
- late-work rules
- attendance rules
- class meeting information
- instructor-specific expectations

Information extracted from documents should retain its source and should not silently override more reliable structured data.

### The LLM is not a source of truth.

The LLM may explain, summarize, reason over retrieved context, generate study recommendations, and help interpret ambiguous text.

The LLM must not invent missing academic data.

---

## Ingestion Design Rules

Do not block the entire UI while every Canvas source and document is processed.

Preferred flow:

1. Load essential dashboard data quickly.
2. Start or continue course ingestion separately.
3. Store normalized results locally.
4. Process only new or changed content on later syncs.
5. Let feature pages read from stored data when possible.

Initial sync may be heavier than future syncs.

Use Canvas IDs, timestamps such as `updated_at`, and stored metadata to avoid unnecessary downloads and reprocessing.

A 403 or 404 from one Canvas endpoint does not mean the entire course is unavailable.

For example, the course-wide Files endpoint may return 403 while individual files referenced by module items remain downloadable.

Do not treat unavailable optional sources as fatal errors.

---

## Class Schedule Rules

The Class Schedule feature should not depend on the user waiting for a complete ingestion process every time the page opens.

Expected behavior:

1. Try structured Canvas schedule data first.
2. If unavailable, inspect ingested syllabus/course content.
3. Extract candidate class meetings.
4. Validate days and times.
5. Store source and confidence.
6. Ask the user to confirm or edit the result.
7. Use only confirmed schedule data for study planning.

Manual entry must always remain available as a fallback.

---

## Priority Engine Rules

Do not rank assignments using raw points alone.

For example, a 25-point lab in a category worth 30% of the final grade may be more important than a 100-point homework assignment in a category worth 10%.

Priority should eventually consider deadline, grading category weight, estimated grade impact, assignment status, estimated effort, and user schedule.

The engine should return both a score and an explanation.

Example:

> High priority because it is due tomorrow, belongs to a heavily weighted lab category, and is still unsubmitted.

Do not ask an LLM to produce the final priority score without deterministic supporting logic.

---

## Security Rules

Never commit or expose real Canvas access tokens.

Secrets belong in environment variables.

Required practices:
- `.env` must remain gitignored.
- `.env.example` may show variable names but never real secrets.
- Do not place tokens in source code.
- Do not place tokens in screenshots, documentation, logs, tests, or example payloads.
- Do not commit credentials.
- Avoid printing sensitive headers or tokens.

Personal Canvas access tokens are currently for development/testing only.

Do not design the production authentication model around users manually sharing developer tokens. Production authentication should eventually use an institution-approved Canvas OAuth or equivalent supported authentication method.

---

## Coding Rules

Before editing code:

1. Read the relevant files.
2. Understand the existing implementation.
3. Identify the smallest change required.
4. Reuse existing patterns where reasonable.
5. Explain any significant architectural change before implementing it.

During implementation:
- Prefer small, focused changes.
- Keep functions and services responsible for one area.
- Avoid unnecessary abstractions.
- Avoid duplicate logic.
- Do not add dependencies unless they provide clear value.
- Do not rewrite unrelated code.
- Do not hardcode course IDs, assignment IDs, file IDs, or user-specific data.
- Preserve existing working behavior unless the task explicitly changes it.
- Use meaningful names.
- Handle API failures explicitly.
- Prefer timezone-aware datetimes.
- Keep Canvas networking outside UI code.

When requirements are ambiguous, do not silently invent product behavior. Surface the ambiguity.

---

## Frontend Rules

The frontend should display real backend data whenever the feature is connected.

Do not replace real data with hardcoded demo values.

For network-dependent pages:
- show a loading state
- show a useful empty state
- show an error state when appropriate

Prefer parallel requests where independent requests can safely run concurrently.

Pages should remain usable even if optional Canvas sources are unavailable.

---

## Backend Rules

API endpoints should expose application behavior, not raw implementation details.

Prefer reusable backend services over duplicating Canvas requests inside multiple API routes.

Use shared pagination and error-handling logic.

Avoid making the browser directly responsible for orchestrating many Canvas API calls if the backend can provide an aggregated endpoint cleanly.

---

## Testing Expectations

Every important behavior change should be verified.

At minimum, consider successful case, empty-data case, unavailable-source case, invalid input, API failure, filtering correctness, and sorting correctness.

Bug fixes should include a regression test when practical.

Do not claim a feature is complete only because the code runs.

Before considering a change complete, run the relevant tests, linting, type checks, application build, and manual verification for UI behavior.

If a required test framework does not yet exist, do not fabricate test results. State what was manually verified and what still needs automated coverage.

---


## Developer Understanding and Change Explanation

The coding agent should help the developer understand the system, not replace the developer's role.

For every meaningful code change:

1. Explain what file is being changed and why.
2. Explain the purpose of each new function, class, or major code block.
3. Describe how the new code fits into the existing data flow.
4. Call out any new dependency, abstraction, API call, or architectural decision.
5. Before making a large change, summarize the planned implementation in plain English.
6. After implementation, summarize:
   - what was added
   - what each important function does
   - how data moves through the new code
   - what remains incomplete
7. Prefer small changes that the developer can review over large generated rewrites.
8. Do not continue adding layers of code if the previous layer has not been explained.
9. If a function is non-trivial, include a short comment or docstring explaining its responsibility.
10. Avoid unnecessary abstractions that make the code harder for the developer to understand.

When asked to implement a feature, prefer this workflow:

Understand existing code
→ explain proposed change
→ implement a small piece
→ verify it
→ explain what was added
→ continue to the next piece

Do not generate large amounts of code without checkpoints unless explicitly requested.

## Change Size Rule

Prefer the smallest complete implementation step.

If a feature requires multiple layers, split it into stages.

Example:

Bad:
"Implement the entire ingestion + RAG + scheduling pipeline."

Better:
1. Add Canvas content discovery.
2. Verify returned data.
3. Add document download.
4. Verify file handling.
5. Add text extraction.
6. Verify extracted text.
7. Add chunking.
8. Add embeddings.
9. Add retrieval.

## Explanation Requirement

Assume the developer wants to understand the code.

Do not only say "implemented successfully."

For any new non-trivial function, briefly explain:
- inputs
- output
- main logic
- why it exists
- what calls it
- what it calls

The goal is for the developer to remain able to explain and maintain the system after agent-assisted changes.

## Agent Behavior Rules

When acting as a coding agent:

### Do
- inspect before editing
- make focused changes
- preserve architecture boundaries
- explain assumptions
- run relevant verification commands
- report exactly what changed
- mention tests that were run
- mention anything that could not be verified
- use existing repository conventions

### Do not
- redesign the entire project without being asked
- introduce MCP or LangGraph prematurely
- use AI where deterministic logic is more appropriate
- hardcode user-specific Canvas data
- expose secrets
- invent Canvas fields or API behavior
- silently change API contracts
- install large dependencies without justification
- delete working code merely because a different design is possible
- mark something complete when significant parts are placeholders
- generate fake test evidence

---

## Current Known Canvas Behavior

Canvas permissions vary by course.

A source may be available, unavailable with 403, unavailable with 404, or present only through module references.

The application must degrade gracefully.

Known example from development:
- a course-wide Files request returned 403
- a file referenced by a module item was still successfully downloaded through its individual file endpoint

Design around actual accessible resources rather than assuming all Canvas sections are enabled for every course.

---

## Development Commands

From the project root:

### Activate Python environment

```bash
source .venv/bin/activate
```

### Run FastAPI backend

```bash
python3 -m uvicorn backend.app.main:app
```

Backend: `http://127.0.0.1:8000`

FastAPI documentation: `http://127.0.0.1:8000/docs`

### Run frontend

In another terminal:

```bash
cd frontend
npm run dev
```

Frontend: `http://localhost:3000`

The frontend terminal does not need the Python virtual environment.

---

## Definition of Done

A feature should not be considered done only because an agent generated code.

A feature is done when:

1. The behavior matches the written requirement.
2. It fits the architecture.
3. It does not expose secrets.
4. Existing relevant behavior still works.
5. Error and empty states are considered.
6. Relevant tests/checks pass.
7. The developer has reviewed the diff.
8. Any remaining limitations are documented.

The coding agent assists development. The repository specifications and the developer remain the authority for product and architecture decisions.
