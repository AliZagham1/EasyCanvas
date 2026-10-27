# Course ingestion: what works now

Ingestion means collecting the course material, reading supported content, and
remembering the results so we can use them later.

For example: find a lecture's Canvas file ID → download its PDF → extract its
text → save that text with the course ID, file ID, timestamps, and page numbers.

## Automatic app flow

Starting the FastAPI backend now starts an ingestion worker automatically. It
fetches the current-course list, ingests every unique course, and saves results
under each course ID. No course names or IDs are hardcoded into this flow.

The worker checks again every 30 minutes while the backend is running. It fetches
the current list each time, so a changed semester/course list is used on the next
sync. Existing snapshots are kept for older courses; they are not mixed with the
new courses' documents.

The Dashboard's **Sync Canvas** button requests an immediate sync and displays
progress. Courses and assignments load independently, and refresh after an
observed sync completes. Navigating away does not cancel ingestion.

```bash
.venv/bin/python -m uvicorn backend.app.main:app
```

`COURSE_SYNC_INTERVAL_SECONDS` can change the interval (minimum 60 seconds).
The worker stops with the backend; it is not a system-wide scheduled task.

The dashboard and ingestion share `get_current_courses()`. It requests active,
published student courses and their term metadata, then selects terms whose
timezone-aware Canvas start/end dates include the current UTC time. It never
ranks numeric term IDs. Undated terms are excluded when a dated current term is
available. If no term can be selected and dates are missing or invalid, set
`CANVAS_TERM_ID` in the local `.env` after listing the account's accessible terms
with `get_course_terms()`. See the root README for the cross-platform setup command.

## Developer commands

Run these commands from the EasyCanvas root. The explicit interpreter avoids
confusing the root `.venv` with a separate `backend/.venv`.

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m backend.app.ingestion_service 283397 --ingest
```

No backend server, frontend server, or database server needs to be running for
this diagnostic command. Replace the course ID to ingest another accessible
course. For a one-shot sync of every current course:

```bash
.venv/bin/python -m backend.app.ingestion_service --all-current --ingest
```

Use the app's Sync Canvas action during normal operation. Run these direct CLI
diagnostics with the automatic backend worker stopped to avoid overlapping
manual processing of the same course.

To try only the first file, add `--max-files 1`. Other file references are
reported as deferred, while native Canvas text is still processed.
Use `--force` to deliberately reprocess unchanged documents.

The old discovery-only command still works without `--ingest`. The individual
file helper can also check extraction without saving anything:

```bash
.venv/bin/python -m backend.app.file_download_test 283397 44094072 --extract
```

The CSE 3320 PowerPoint case uses the same commands and automatic reader selection:

```bash
.venv/bin/python -m backend.app.file_download_test 278484 44287012 --extract
.venv/bin/python -m backend.app.ingestion_service 278484 --ingest
```

For the older Word assignment in that course:

```bash
.venv/bin/python -m backend.app.file_download_test 278484 45160347 --extract
```

## Which file does what?

| File / function | What goes in | What comes out |
| --- | --- | --- |
| `canvas_service.py` | Course, module, page, or file IDs | Real Canvas API responses and downloaded bytes; handles pagination, timeouts, temporary retries, and authentication |
| `content_discovery.py` / `build_file_inventory()` | The discovered course records | Unique file IDs with the sources and modules that referenced them; recognizes native file links and attachment metadata |
| `ingestion_service.py` / `sync_course()` | One course ID | An organized course snapshot with source availability; optional page bodies include recovery through module page references |
| `ingestion_service.py` / `ingest_course()` | One course ID and optional file limit | Saved documents plus a short outcome report; checks changes and passes downloaded bytes to the reader |
| `ingestion_service.py` / `ingest_current_courses()` | The current account's fresh Canvas course list | Runs the existing per-course pipeline for each unique ID, keeps successes, and reports course failures independently |
| `course_sync_service.py` | Startup, timer, or queued sync request | A separate local worker process, durable progress, periodic checks, and cleanup when the backend stops |
| `document_service.py` | File bytes or API-provided HTML | Plain text, hashes, extraction status, PDF pages, PowerPoint slides, Word blocks, or notebook cell offsets; never makes network requests |
| `legacy_office.py` | Legacy DOC/PPT bytes | Temporary conversion to DOCX/PPTX for the existing readers; handles converter discovery, private profiles, timeouts, and cleanup |
| `ingestion_store.py` / `IngestionStore` | Course snapshots and extraction results | SQLite storage and methods to read saved documents after restarting Python |

The document readers use `pypdf`, `python-docx`, and `python-pptx`. SQLite is
already included in Python. Notebook parsing uses Python's JSON library and
never runs notebook code. The Office libraries read document contents in memory;
we do not need to open the Word or PowerPoint applications.

`extract_docx(bytes)` returns paragraphs and nested tables in document order,
plus available headers and footers. Each block keeps its location and block
number. Word page numbers are not guessed: page layout depends on rendering.

`extract_pptx(bytes)` returns each slide's text, tables, grouped text, and speaker
notes. Each slide keeps its original number, including blank/image-only slides.
`notes_start_character` identifies the notes within that slide's extracted text.
The reader uses the stored shape order; it does not reproduce visual layout.

`extract_file(metadata, bytes)` selects a reader from the filename or MIME type.
`file_pipeline_version(metadata)` includes the Office library version, so a reader
upgrade invalidates its Office cache without forcing unchanged PDFs to download.

## Why save it?

The first sync downloads supported files and extracts their text. Later syncs
still ask Canvas for current records and metadata, because content and access
can change. Matching timestamps, size, and parser version let us skip unchanged
file downloads and extraction. HTML content is compared using a content hash.

If a file has no useful modification timestamp, it must be downloaded to compare
its hash. Identical bytes can still skip parsing. A temporary signed download
URL changing does not by itself make the course material new.

Text and metadata are saved automatically in `backend/data/easycanvas.sqlite3`.
Modern file binaries are kept in memory only and discarded after processing.
Legacy conversion briefly writes input/output files in a private temporary
folder, which is removed on success and failure. Original binaries and converted
files are never permanently stored by the ingestion pipeline.
PDF pages, PowerPoint slides, and Word blocks store text once; their combined
text is rebuilt on read. This folder is gitignored; it contains private course data.

Caches are separated by Canvas installation and a token-derived namespace. The
token itself is not saved to SQLite. Rotating the development token starts a
fresh namespace. This local development arrangement is not production account
authentication; PostgreSQL and supported Canvas OAuth remain future work.

## Failures and incomplete coverage

A Files-listing 403 does not stop individual referenced file downloads. An
unavailable source or file is recorded and other documents continue processing.
Records from a partially failed paginated response are preserved.

Failed refreshes retain previous readable text but explicitly mark it stale.
Documents not rediscovered in the latest sync remain saved but are excluded from
the current-document list. Nothing is silently declared deleted after a 403.

Future retrieval should use `store.load_documents(course_id, readable_only=True)`:
this excludes stale, unavailable, unsupported, and empty results. Partial text
remains usable with its limitations and page/cell metadata preserved.

## Supported content and remaining limits

- Native Canvas syllabus, assignment instructions, page bodies, quiz descriptions,
  announcement messages, and discussion-topic messages are read when accessible.
- PDF text, HTML, UTF-8/UTF-16 TXT/MD/CSV files, DOC/DOCX, PPT/PPTX, and version-4 Jupyter
  notebooks are supported. Other text files can also be read when Canvas supplies
  a text MIME type, such as the C source files found in CSE 3320.
- PDF pages with no extractable text are marked `no_text`; a partly readable PDF
  is `partial`. Empty pages and image-only pages both need inspection before
  assuming the missing content requires OCR.
- Notebook instructions and code are preserved. Outputs and embedded images are
  not extracted, and their presence marks the notebook partial.
- DOCX paragraphs, tables, headers, and footers are read. Images, equations,
  text boxes, footnotes, content controls, and tracked changes are flagged when
  detected but are not extracted. Readable text is retained with a partial status.
- PPTX text, tables, grouped text, and speaker notes are read. Images, charts,
  diagrams, embedded content, and equations are flagged when detected. Their
  meaning cannot be inferred merely because surrounding slide text was read.
- Legacy Word/PowerPoint 97–2003 `.doc` and `.ppt` are converted locally and then
  read with the existing DOCX/PPTX readers. RTF saved with a `.doc` extension is
  also accepted by the converter. Macros and active content are disabled.
- Legacy Word conversion can change table boundaries and other structure.
  Converted `.doc` text is explicitly `partial` with a
  `legacy_doc_structure_unverified` warning, even when readable text is available.
  On macOS, the built-in `textutil` reader prepares table-bearing Word documents
  as temporary HTML, preserving a trailing table that direct LibreOffice import
  lost in a regression fixture. Other platforms use direct LibreOffice import.
  The converted structure is not a verified copy of the original layout.
- Images, diagrams, handwritten content, audio/video, and external/LTI
  tools are not understood by this implementation. Unsupported formats are
  reported without downloading their bytes.
- Discovery does not crawl external sites or recursively follow every link in
  downloaded documents. Module references can recover pages even when the Pages
  index is unavailable; other undiscovered pages cannot be assumed accessible.
- File downloads are limited to 50 MiB. PDF/PPTX extraction is limited to 1,000
  pages/slides per file. Office ZIP contents are checked before parsing, with a
  100 MiB uncompressed size limit and a 10,000-part limit. Legacy conversion has
  a 60-second process timeout and a 50 MiB converted-file limit.
- A finished run can still have partial coverage. Its summary is the evidence;
  it does not mean the future AI knows everything in the course.

The existing dashboard and Upcoming assignment data flow continues to use live
Canvas-backed endpoints. Document ingestion runs separately from those requests.
OCR, embeddings, RAG, and an AI assistant are not implemented by this step.

## Background job and API

- `GET /api/ingestion` returns the configured account's latest job status, current
  course, per-course outcomes, counters, and next scheduled check.
- `POST /api/ingestion/sync` returns HTTP 202 immediately. The backend discovers
  course IDs; the browser does not orchestrate course-content requests.
- Repeated clicks during a queued/running job reuse that job. An account/database
  lock prevents duplicate background workers. The `filelock` package supplies
  cross-platform locking; Windows workers use console process groups and a break
  signal for shutdown, while POSIX workers use sessions and SIGINT.
- Job state is stored in SQLite alongside course data. A new worker restarts an
  interrupted sweep and reuses already extracted content. Original error URLs,
  tokens, and full document text are not included in job status responses.
- A missing optional source or failed course does not stop the others. A
  completed job with partial coverage means the sweep finished; it does not
  certify that every document was fully read.
- FastAPI starts/stops the worker through its lifespan. The worker has its own
  Python process, so document parsing does not occupy the web process. Graceful
  shutdown interrupts conversion and lets temporary-file cleanup run.
- This is a local development worker for the configured Canvas account. It is
  not a production multi-user authentication or distributed job-queue system.

The live all-course run processed all six currently returned courses and saved
338 readable documents, including 68 for CSE 4321. Some documents are partial.
These counts describe that run, not hardcoded product values.

## Legacy converter setup

Legacy files need the LibreOffice system application in addition to the Python
requirements. It has been installed on this development Mac. A new Mac can use:

```bash
brew install --cask libreoffice
```

On Linux, install LibreOffice Writer and Impress through the system package
manager. The service looks for `soffice`/`libreoffice` on PATH and the standard
macOS application location. An optional `LIBREOFFICE_PATH` environment variable
can specify another executable location. No office server is started.

`convert_legacy_office(bytes, format)` accepts `doc` or `ppt`, converts in a
private temporary directory, and returns modern Office bytes. `extract_legacy_office()`
passes those bytes to our readers and restores the original file hash and source
format. Metadata keeps `converted_format`, the converter version, and extraction
warnings. Converter changes invalidate legacy caches without invalidating PDFs.

Each conversion uses a separate user profile with macros/active content disabled
and link updates restricted. Child processes do not receive Canvas tokens or AI
keys. Timeouts stop the conversion's process group. Missing LibreOffice is an
explicit unavailable result; other course documents can still be processed.

## Verify

```bash
.venv/bin/python -m unittest discover -s backend/tests
```

Run the real binary DOC/PPT conversion tests as well when LibreOffice is installed:

```bash
EASYCANVAS_TEST_LEGACY_CONVERSION=1 .venv/bin/python -m unittest discover -s backend/tests
```

Tests cover discovery, pagination failures, retries, file access, real generated
PDF/DOCX/PPTX fixtures, text and notebook extraction, persistence, cache
invalidation, permission changes, incomplete content, Office package limits,
and incremental reuse. Live Canvas checks are separate from those mocked HTTP
tests and depend on the current token and course permissions.

The CSE 3320 live run discovered 21 unique files despite the Files-listing 403,
read four PPTX files and `Lab 3.docx`, and later extracted 2,391 characters from
the older `OSProgAssignment4.doc`, keeping its Word-structure warning. Real binary
DOC/PPT fixtures also test tables, slide order, and speaker notes. This is evidence
for those accessible resources and test cases, not complete coverage for every document.

Reader API references: [Word document blocks](https://python-docx.readthedocs.io/en/latest/api/document.html),
[PowerPoint shapes](https://python-pptx.readthedocs.io/en/latest/api/shapes.html),
and [speaker notes](https://python-pptx.readthedocs.io/en/latest/user/notes.html).
Conversion references: [LibreOffice conversion filters](https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html)
and [command-line parameters](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html).
