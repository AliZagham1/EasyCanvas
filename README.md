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

Stack: Next.js, React, TypeScript, Tailwind CSS; Python, FastAPI, Requests, and SQLite.
RAG, embeddings, Study Assistant, priorities, class schedules, study plans, MCP,
LangGraph, and PostgreSQL migration are **planned**, not implemented features.

## Prerequisites

- Git and access to the team's repository/branch.
- Python **3.11+**; development uses Python 3.13.
- Node.js **20.9+** and npm.
- Your own Canvas access token and internet access.
- LibreOffice Writer/Impress for older `.doc`/`.ppt` files. It is not needed for
  modern `.docx`/`.pptx` files or the normal unit tests.

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
database. Course text and metadata are private and remain on each developer's computer.

For legacy DOC/PPT support, install LibreOffice on that computer and make
`soffice`/`libreoffice` available on PATH, or set `LIBREOFFICE_PATH` to its executable.
On macOS, the standard application installation is also detected. Windows users
must point to the executable inside their own LibreOffice installation if needed.

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
- No OCR, image/diagram understanding, ZIP-content reader, external/LTI integration,
  or recursive crawling of all document links. Some extraction results are partial.
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
