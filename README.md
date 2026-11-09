# EasyCanvas

EasyCanvas is a local academic dashboard for students using Canvas LMS. Each
teammate runs the same code with their own Canvas account and gets their own
courses, assignments, and accessible course materials.

## What works

- Dashboard and Upcoming page with real courses, future unsubmitted assignments,
  due dates/times, loading states, and partial-fetch handling.
- Course-content discovery across accessible Canvas-native sources, including
  syllabus, assignments, modules, pages, files, quizzes, announcements, and discussions.
- Text extraction from supported PDF, HTML, text/Markdown/CSV, DOCX, PPTX, and
  Jupyter notebook files. Older DOC/PPT files use LibreOffice.
- Local SQLite storage, reuse of unchanged extractions, and background ingestion
  at backend startup, every 30 minutes, or through **Sync Canvas**.
- Location-preserving chunking, automatic incremental OpenAI embedding indexing, and
  course-scoped cosine-similarity search with source references. Indexing/search
  require your own `OPENAI_API_KEY` and API credits. Only missing eligible inputs
  are indexed after sync or resumed at startup; automatic updates can be disabled in Settings.
- A course-scoped Study Assistant with streamed conversations, fresh follow-up
  retrieval, validated citations and explicit insufficient-context responses.
- Intent-aware assistant routing for assignment explanations, document overviews,
  saved Canvas rubrics, and current deadlines/submission states/points. See the
  [Study Assistant spec](docs/specs/study-assistant.md) for data boundaries and limits.

Stack: Next.js, React, TypeScript, Tailwind CSS; Python, FastAPI, Requests, and SQLite.
Priorities, class schedules, study plans, MCP,
LangGraph, and PostgreSQL migration are **planned**, not implemented features.

## Prerequisites

- Git and access to the team's repository/branch.
- Python **3.11+**; development uses Python 3.13.
- Node.js **20.9+** and npm.
- Your own Canvas access token and internet access.
- LibreOffice Writer/Impress for older `.doc`/`.ppt` files and visual rendering
  of Word/PowerPoint. Native modern Office text extraction works without it.

The worker uses cross-platform file locking and Windows console process groups.
Windows branches have regression tests; a full native Windows desktop smoke test
has not yet been performed. Run the backend in a terminal such as PowerShell.

## Clone and configure

Replace `TEAM_BRANCH` and `REPOSITORY_URL` with the values shared by your team:

```bash
git clone --branch TEAM_BRANCH REPOSITORY_URL EasyCanvas
cd EasyCanvas
```

Create **one virtual environment in the repository root**.

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of
`python` below; activation is not required when using that interpreter directly.

Install backend dependencies:

```bash
python -m pip install -r backend/requirements.txt
```

Edit the root `.env` file in your editor. Replace both placeholder values. For UTA:

```dotenv
CANVAS_URL=https://uta.instructure.com
CANVAS_TOKEN=paste_your_own_token_here
```

Use the Canvas site base URL, without a course path or `/api/v1`. Each developer
uses their own token. Never commit `.env`, paste tokens into source files, or put
tokens in frontend environment variables. Restart the backend after changing
configuration; existing terminal environment variables take precedence over `.env`.

## Current courses and semester selection

### Optional cleanup of previous semesters

Open **Settings → Course storage** to preview saved course materials. Cleanup is
manual: no course is deleted on startup or when a semester changes. A course is
eligible only after its confirmed Canvas term ends plus a 30-day grace period,
and after all current courses have completed a successful sync. Current courses,
unknown term dates, and courses marked **Keep materials** are protected. Empty or
failed Canvas responses never enable deletion. A fixed `CANVAS_TERM_ID` override
must be cleared before cleanup.

Select eligible courses, review the estimated size, and confirm deletion. This
removes local documents, extracted text, chunks, embeddings, and visual extraction
caches. Saved conversations and minimal course identity remain; Canvas files are
untouched. Old chats remain stored locally, but the assistant course picker still
lists current courses only. Existing saved courses with unknown dates stay
protected; future successful current-course syncs retain Canvas's term dates.
The database is compacted after cleanup where possible; payload estimates differ
from disk savings, and a busy database may reuse free pages instead of shrinking.

### Course selection

EasyCanvas requests active, published student courses from Canvas and selects
terms whose timezone-aware start/end dates include the current UTC time. It does
**not** assume the largest term ID is current. Undated terms are excluded when a
dated current term is available. If dates cannot establish a current term, the
backend reports a configuration error instead of guessing.

To list the terms accessible to your own account:

```bash
python -c "import json; from backend.app.canvas_service import get_course_terms; print(json.dumps(get_course_terms(), indent=2))"
```

If necessary, put the chosen term's `id` in `CANVAS_TERM_ID` in your local `.env`.
Leave it blank for date-based selection. An explicit choice filters your active
student courses to that term; it does not grant access to another account's courses.

Other optional settings are documented in `.env.example`: sync interval
(`COURSE_SYNC_INTERVAL_SECONDS`, minimum 60 seconds) and converter executable
(`LIBREOFFICE_PATH`, only when LibreOffice cannot be found automatically).

## Start the app

Terminal 1, from the repository root with the Python environment activated:

```bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Backend: <http://127.0.0.1:8000>; API docs: <http://127.0.0.1:8000/docs>.
Starting it also starts background ingestion. The initial sync can take time;
essential dashboard data loads separately.

Terminal 2, from the repository root:

```bash
cd frontend
npm ci
npm run dev -- --port 3000
```

Open <http://localhost:3000>. Keep ports **8000 and 3000** available: API URLs and
development CORS currently use these local ports. The frontend terminal does not
need Python activation. A fresh frontend build downloads its Google font.

## Manual ingestion

With the backend stopped, run from the repository root:

```bash
python -m backend.app.ingestion_service --all-current --ingest
```

This discovers your courses automatically; no course IDs need editing. During
normal app use, use **Sync Canvas** instead of running an overlapping CLI sync.
The old root `test.py` is a course-specific development example, not an app entrypoint.

SQLite creates `backend/data/easycanvas.sqlite3` and its tables automatically.
There is no database server or manual schema setup. Do not copy a teammate's
database. SQLite stays on each developer's computer. Enabled embedding, RAG and
Vision requests send the required course content to OpenAI.

For legacy DOC/PPT support, install LibreOffice on that computer and make
`soffice`/`libreoffice` available on PATH, or set `LIBREOFFICE_PATH` to its executable.
On macOS, the standard application installation is also detected. Windows users
must point to the executable inside their own LibreOffice installation if needed.

### Visual course documents

PDF inspection detects image-based questions and vector content even when a page
has some native text. Word/PowerPoint visual rendering also uses LibreOffice.
Paid OpenAI visual extraction is opt-in in your root `.env`:

```dotenv
EASYCANVAS_VISION_ENABLED=true
EASYCANVAS_VISION_MODEL=gpt-6-luna
```

Use your own `OPENAI_API_KEY`. Enabled ingestion sends flagged page images to
OpenAI, saves their extracted content locally, and reuses cached results. It does
not perform visual extraction during an assistant question. With Vision disabled
or unavailable, unread visual material remains explicitly partial.

To backfill already-discovered files, with the backend stopped:

```bash
python -m backend.app.ingestion_service --all-current --enrich-saved
```

This refreshes necessary extractions/chunks. Restart the backend to automatically
index missing eligible text, or use Update course search if automatic updates are off.
Source documents and successful visual extractions
are independent of chunks and embeddings. See the feature spec for limits.

## Checks

From the repository root (no Canvas token or live Canvas requests are needed):

```bash
python -m unittest discover -s backend/tests
```

Real legacy Office conversion tests are opt-in; see the
[ingestion guide](docs/specs/ingestion-pipeline.md). For frontend checks:

```bash
cd frontend
npm run lint
node --test tests/coursework.test.mjs tests/course-sync.test.mjs
npm run build
```

## Current limitations

- Source permissions vary. An unavailable optional endpoint does not stop other
  sources; ingestion completion does not mean every document was readable.
- Visual extraction is fallible: small text, handwriting, math, diagrams and
  conversion changes can remain unreadable or partial. It does not certify every
  statement in a document. ZIP-content reading, external/LTI integration and
  recursive crawling of all document links remain unsupported.
- Legacy Word conversion can alter structure, and its macOS-specific table
  workaround is unavailable on other systems. See the ingestion guide for limits.
- The same Canvas file ID is deduplicated within a course. Identical uploads with
  different IDs are not yet deduplicated by content across records.
- This is local development with personal tokens, not production multi-user login.
- `.gitignore` protects the known private/output paths; it cannot detect a token
  manually pasted into a source file. Keep exports in ignored private directories.

## Project structure

```text
backend/app/main.py                FastAPI routes and application lifecycle
backend/app/canvas_service.py      Canvas requests and current-course selection
backend/app/ingestion_service.py   Discovery and incremental ingestion
backend/app/course_sync_service.py Background worker and persistent job progress
backend/app/content_discovery.py   Unique Canvas file references
backend/app/document_service.py    Document text readers
backend/app/visual_inspection.py   PDF page risk detection and bounded rendering
backend/app/vision_service.py      Replaceable OpenAI visual extraction provider
backend/app/visual_document_service.py Native/visual extraction and page cache reuse
backend/app/legacy_office.py       Temporary legacy Office conversion
backend/app/ingestion_store.py     Local SQLite storage
backend/tests/                    Automated backend tests
frontend/app/                     Pages, sidebar, fonts, and styles
frontend/lib/                     Frontend data fetching and shared helpers
frontend/public/                  Public application assets
docs/specs/ingestion-pipeline.md   Detailed implemented ingestion behavior
```

[AGENTS.md](AGENTS.md), [PRODUCT.md](PRODUCT.md), and [DESIGN.md](DESIGN.md) describe
project rules, product direction, and the visual system. Planned features in those
documents should not be treated as working functionality.

The Study Assistant supports SQLite-backed, course-isolated conversations and real
streamed answers. It retrieves fresh course evidence for follow-ups and validates
citations before showing final sources. Returning to a course restores its most
recently active thread; the Conversation selector reopens older threads. New
conversation creates a separate saved thread. History survives backend restarts.
Rename changes its saved display name; Delete conversation asks for confirmation
and removes only that chat and its messages, keeping course materials intact.
Older chats that existed only in memory cannot be recovered retroactively.
Run one backend worker: active-stream locks remain in memory. Current assignment
lists/deadlines/points/submission states use structured Canvas data, not document RAG.
`EASYCANVAS_QUERY_MODEL` optionally selects the follow-up query model; it defaults
to the answer model. Both use the existing backend-only `OPENAI_API_KEY`.

Frontend checks (from `frontend/`): `npm test`, `npm run lint`, `npm run build`,
and `npm run test:e2e` after building. Browser tests use fake backend responses;
install their browser once with `npx playwright install chromium`.

### Keeping Study Assistant search current

**Settings → Study Assistant search** contains **Keep Study Assistant search
automatically updated**, enabled by default. A successful Canvas sync queues only
missing/new/changed eligible text for embedding. The existing background worker
processes the queue, reuses unchanged vectors, and resumes interrupted work after
restart. Document extraction and successful embedding batches are never discarded
when an indexing request fails. Automatic failures wait at least 15 minutes and
retry on a later successful sync; **Retry indexing** retries immediately.

The Study Assistant shows readiness for the selected course. Settings expands each
course to show per-document indexed/pending/failed passage counts and the last
completed update. Counts include every eligible source passage; identical text
within one account/course shares one embedding. A course may have more passages
than unique embedding inputs. Removed, stale and ineligible text stays excluded
from retrieval, and template-policy filtering is unchanged. During Canvas sync,
coverage is explicitly marked as updating rather than claiming complete readiness.

Turn automatic updates off to use **Update course search** manually. Search itself
does not silently index a course. Both options use your own `OPENAI_API_KEY` and
API credit. No terminal embedding command is required during normal app use. If
you ingest/backfill with the backend stopped, restart it to process saved work.

Settings shows **EasyCanvas tracked API usage**: this month's estimate has the
main emphasis, with monthly feature totals, today's estimate, tracked requests,
and token counts underneath. A configured positive monthly warning has a progress
bar; an unset or zero warning never invents a percentage. The tracking-start date
and lifetime tracked estimate remain visible. Recording begins with this feature;
earlier usage is not reconstructed. Provider response IDs prevent duplicate accounting. Unknown
usage or unknown model rates are counted as unpriced calls, not as free usage.
Rates are dated standard-processing estimates in `api_usage_service.py`, including
reported cached-input discounts. These figures are **not OpenAI account billing**.
OpenAI's billing dashboard is the authoritative account total; calls made before
local tracking or outside EasyCanvas are not included. For easier comparison in
the future, use a dedicated OpenAI project/API key for EasyCanvas. This is an
optional setup recommendation; the app does not change projects or credentials.

The optional monthly USD warning starts unset, is persisted locally, and does not
block calls or act as a hard spending cap. Disable automatic indexing to pause
background embedding work; interactive assistant/Vision settings remain separate.

Implementation: `search_index_service.py` coordinates the existing incremental
embedding service; SQLite stores `search_index_jobs`, `app_settings`, and a
secrets-free `api_usage` ledger. Usage history is kept when old course materials
are cleaned up. Read [the stability log](docs/STABILITY_LOG.md) before investigating
a recurring demo/production symptom.
