# Course ingestion: what works now

Ingestion means collecting the course material, reading supported content, and
remembering the results so we can use them later.

For example: find a lecture's Canvas file ID → download its PDF → extract its
text → save that text with the course ID, file ID, timestamps, and page numbers.

## Automatic app flow

Paid visual processing is enabled with `EASYCANVAS_VISION_ENABLED=true` in the
root `.env` (the example config enables it). Restart the backend after changing
the setting; an explicit shell environment value takes precedence over `.env`.
Keep it false for inspection-only work. Automated tests use fake providers.

`enrich_saved_course()` backfills only still-pending/failed visual content and
keeps completed per-page transcriptions cached. Enabling Vision alone does not
invalidate complete compatible extractions. Generic `partial` status from a
non-visual issue is not a reason for another paid call. A completed visual
transcription with genuinely unreadable regions remains partial and is not
repeatedly paid for. Saved external syllabus files reuse the existing allowlisted
downloader; syllabus discovery/authentication are unchanged.

Only changed documents are rechunked. `embed_course(..., document_keys=[...])`
can then index missing exact-text inputs from affected documents, reusing cached
vectors for unchanged text and preserving course/account boundaries.

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
| `chunking_service.py` / `chunk_document()` | One readable extracted document | Ordered chunks with page, slide, Word block, notebook cell, or plain-text locations; no network calls |
| `chunking_service.py` / `ensure_course_chunks()` | A course ID and the existing store | Backfills missing chunks, reuses unchanged chunk sets, and replaces changed sets |

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

Refresh health and processed-content usability are separate. DNS/connection
errors, timeouts and retryable HTTP 408/429/500/502/503/504 retain unchanged,
readable cached material as `cache_status: stale_but_usable`. Existing chunks and
embeddings remain available without reprocessing. The latest attempt/error stays
recorded; an unsuccessful refresh is never reported as a successful one.

Document readers expose `content_status`, `refresh_status`, `content_usable`,
`cache_status`, `last_successful_refresh` and `last_refresh_error`. The successful
timestamp is recorded for future successful saves; legacy records without that
history keep an unknown timestamp. The compatibility `stale` exclusion flag
remains true for unusable mismatches, not for usable transient-failure fallback.
Read-derived fields are not copied back into stored attempts.

Confirmed 401/403/404/410 responses, locked access, known changed versions/bytes,
parser/TLS/unknown processing failures and invalid cached text do not qualify for
temporary fallback. A `cache_blocked` marker persists confirmed invalidation
through later network failures; only successful processing clears it. Text may
remain on disk for diagnosis while retrieval excludes it. A later successful
download or conditional validation clears refresh errors normally. A successful
unchanged Canvas metadata check can reuse the saved extraction without another
binary download. No DNS configuration, allowlist or credential handling changes.
Documents not rediscovered in the latest sync remain saved but are excluded from
the current-document list. Nothing is silently declared deleted after a 403.

Future retrieval should use `store.load_documents(course_id, readable_only=True)`:
this excludes unusable stale, unavailable, unsupported, and empty results while
allowing valid transient-failure caches. Partial text
remains usable with its limitations and page/cell metadata preserved.

## Document chunking

After `ingest_course()` saves extracted documents, it calls
`ensure_course_chunks(course_id, store)` in the existing ingestion worker. The
dashboard's essential requests remain separate. Chunking reads SQLite; it never
downloads files, runs notebook code, or calls an AI model.

To chunk an already-saved course without running Canvas discovery or downloads:

```bash
.venv/bin/python -m backend.app.chunking_service 283397
```

Replace the example course ID with an accessible course already saved for the
configured account. The existing Canvas configuration identifies that account's
local namespace; this command makes no Canvas requests. Run it with the normal
backend worker stopped to avoid overlapping processing. It prints only processed,
reused, and chunk counts, never document text or download URLs.

The starting maximum is 2,000 Python characters per chunk, with up to 200
characters repeated when splitting a long source unit. These are character counts,
not embedding-model token counts. Splitting prefers paragraphs, lines, then spaces;
unbroken text has a character-boundary fallback. Short units stay intact.
Overlap starts advance to a clean word/whitespace boundary within the 200-character
allowance. A very long unbroken token keeps the character fallback, so no source
text is skipped just to avoid a split.

- PDF pages and PowerPoint slides are never combined across boundaries.
- Consecutive small Word blocks can share a chunk when their location matches.
  All contributing block numbers, paragraph/table kinds, and locations survive.
  Page numbers are not invented for Word documents.
- Notebook cells remain separate, retaining cell number/type and source offsets.
- Native Canvas HTML, downloaded HTML, and text/Markdown use the extracted plain
  text. HTML heading semantics are not reconstructed from the original markup.
- Empty/unreadable units are skipped. Partial readable documents remain usable;
  document warnings and available per-unit warnings accompany returned chunks.

`locations` contains end-exclusive character ranges. For pages, slides, and Word
blocks, ranges refer to that individual unit's extracted text. For plain text
and notebooks, they refer to the document's full extracted text. Notebook cell
bounds and PowerPoint speaker-note offsets are also retained.

Two tables are added automatically, without replacing existing course/doc data:

- `document_chunk_state`: account namespace, course/document identity, hash of
  extracted text and location structure, chunker version, count, and processing time.
- `document_chunks`: account namespace, course/document identity, chunk index,
  input hash, chunker version, text, and JSON locations.

Chunk rows and completion state are replaced together in one transaction. A
failed replacement rolls back to the previous complete set. A completed empty
set is distinct from a document that has not been processed.

Matching extracted content/locations and `CHUNKER_VERSION` reuse existing chunks.
Changed content or location structure rebuilds only that document. A chunker
version change rebuilds from saved extraction, without forcing another download.
Update `CHUNKER_VERSION` whenever changing the splitting algorithm or its settings.
Titles, source URLs, timestamps, and module references are read from the parent
document rather than copied into every chunk row, so metadata-only changes do
not require splitting text again.

Use `store.load_chunks(course_id, document_key=None)` to read chunks. It returns
source identity/title/URL, quality information, module references, and locations.
It excludes chunks from unusable stale, unavailable, empty, or undiscovered documents,
and excludes sets whose input hash or chunker version no longer matches. Old
rows can remain on disk for recovery, but are not returned as current content.

### Eligibility, source quality, and future embedding reuse

`load_chunks()` also returns `embedding_eligible`, `eligibility_reason`,
`text_hash`, `embedding_reuse_key`, and `source_quality`. These are deterministic
metadata derived on read from the saved chunk and its current parent document;
they do not need new database columns or a text rebuild for metadata-only changes.
No source extraction or chunk is deleted by eligibility filtering.

The conservative low-information rules mark these ineligible:

- Page/slide text consisting only of a short page-number label.
- A standalone filename with a recognized document/image/archive extension and
  no spaces. Ambiguous text such as `cat notes.txt` is retained.
- A few exact boilerplate labels: `thank you`, `the end`, `end`, `last`, and
  `about the course` (ignoring case and surrounding label punctuation).
- A page/slide containing only the document's filename stem, when that same bare
  title occurs at least three times in the document (for example, repeated `Disks`
  slides in `Disks.pptx`).

There is no minimum-length cutoff. Other short headings, formulas, matrices,
commands, and text remain eligible. Notebook code cells and recognized source-code
files are exempt from these label rules. Some ambiguous low-value chunks deliberately
remain eligible rather than risking silent loss of useful course information.
Use `load_chunks(..., eligible_only=True)` to exclude flagged chunks from later work.

`load_embedding_inputs(course_id)` groups eligible, byte-for-byte identical UTF-8
chunk text into one input with a `sources` list containing every original
document/chunk identity, location, source quality, and reference. The reuse key
includes the account namespace, course ID, and exact-text SHA-256. Different
accounts/courses never share a reuse key; whitespace/case differences are not
normalized. Future embedding caches must additionally include model/version and
configuration. This helper prepares data only: no embedding model or vector store
is called. Separate uploads still retain their extraction and chunk records.

For native syllabus/page sources, `source_quality.flags` contains
`instructor_template_guidance` when explicit template editing instructions match:
`verify your syllabus` together with `syllabus template`; `this page should remain
unpublished` together with `review the following`; or the exact instruction
`please review the content of this section before publishing this page`.
Whitespace and case are normalized only for this detection. Flagged sources have
`policy_retrieval_eligible: false`; their text remains saved and can still be
inspected. A true value means no detected exclusion, not verified instructor review.
The flag is preserved per origin even when text inputs are grouped for reuse.

Missing native syllabus URLs receive the configured Canvas base plus the known
`/courses/{course_id}/assignments/syllabus` route, only when a valid HTTP(S) base
and positive course ID are available. Supplied links are preserved. This applies
to new discovery and old saved syllabus documents on read, without network calls.
The route is used by [Canvas itself](https://github.com/instructure/canvas-lms/blob/master/lib/course_link_validator.rb).

The chunking stage itself makes no embedding, OCR, or RAG calls.

## Incremental embeddings and semantic retrieval

`embedding_service.py` uses OpenAI `text-embedding-3-small` at 1,536 dimensions.
Set `OPENAI_API_KEY` in the local root `.env`; the key is never saved in SQLite
or included in reports. This stage uses the existing `requests` dependency.
After successful ingestion saves current chunks, `search_index_service.py` queues
only missing eligible inputs. The existing background worker drains saved jobs
and recovers interrupted jobs at startup. Automatic updates are persisted in
Settings and default ON; OFF leaves pending counts and an Update course search
action. Search itself never silently indexes a course.

Optional developer commands, with the normal ingestion worker stopped:

```bash
python3 -m backend.app.embedding_service --course 283397
python3 -m backend.app.embedding_service --all-current
python3 -m backend.app.retrieval_service 283397 "What is reduced echelon form?"
```

The all-current command uses the last saved sync's course list, not a fresh
Canvas request. Indexing reads existing current chunk sets; run chunking first
if the saved extraction does not have current chunks. Searching embeds only the
question and never starts document indexing implicitly.

`embed_course()` reads `load_embedding_inputs()`, skips cached text hashes, and
sends at most 32 texts per request. Inputs are conservatively limited to 8,000
UTF-8 bytes; they are rejected rather than silently truncated. Provider indices
restore original input order. Counts, dimensions, finite/nonzero numerical
values, model identity, and available token usage are validated. Transient
connection failures, HTTP 429, and selected server errors retry up to three
attempts; authentication and `insufficient_quota` billing errors do not retry. Raw provider messages/headers and
course text are not printed in indexing errors.

The new `chunk_embeddings` table stores namespace, course ID, exact-text hash,
provider, model, configuration fingerprint/JSON, dimensions, vector bytes, and
creation timestamp. The primary key includes account/course/text/provider/model/
configuration. Vectors use little-endian float32 binary data, four bytes per
dimension. Source titles, URLs, and locations are not duplicated into this table.
Successful batches commit separately, so rerunning after a failure reuses earlier
successes. A model/configuration change creates a separate cache set. There is no
question-vector cache or approximate vector index in this prototype.

`search_course()` filters the current eligible inputs, removes policy-ineligible
template origins before ranking (even inside duplicate groups), and uses only
that account/course/model/configuration's cached vectors. Orphaned vectors do
not make stale/removed/ineligible documents searchable. Python cosine similarity
ranks passages, with deterministic text-hash tie-breaking. A score measures
similarity, not confidence or policy authority. Results include all remaining
source identities, titles, URLs, page/slide/block/cell offsets, quality flags, and
warnings. Word blocks/cells retain their existing location conventions.

Search reports `ready`, `partial`, `not_indexed`, or `no_content`, with indexed and
missing input counts. Empty/missing indexes do not call the embedding provider.
No other course/model is used as a fallback. Exact Canvas deadlines/submission
states still belong to the structured data flow, not semantic search.

Automated embedding/retrieval tests use fake providers and mocked HTTP responses;
they consume no API credits. Real indexing/search checks are separate paid calls.
This embedding stage implements numerical semantic retrieval. The separate
answer-generation stage is described below. OCR, chatbot/frontend changes,
PostgreSQL, vector databases, MCP, and LangGraph remain outside this stage.

## Stateless backend RAG answers

`rag_service.answer_question(course_id, question)` reuses `search_course(top_k=5)`.
It labels up to five unique passages `S1`–`S5`, preserves valid duplicate origins,
and calls `llm_service.OpenAIAnswerProvider`. The latter uses the existing
`OPENAI_API_KEY` with the Responses API, `store: false`, no tools, no previous
conversation, and a strict JSON response schema. `EASYCANVAS_ANSWER_MODEL` selects
the answer model; the initial default is `gpt-6-luna`. This does not change
`text-embedding-3-small` or require rebuilding document embeddings.

```bash
python3 -m backend.app.rag_service 278484 "What are the advantages and disadvantages of FCFS?"
```

The CLI is backend-only. There is no assistant FastAPI endpoint or frontend UI,
no answer/conversation database table, and no automatic question routing. Asking
a question embeds that question and sends retrieved text to the answer model;
it never downloads or indexes course documents implicitly.

The prompt requires course-grounded, student-friendly explanations, cited
claims, explicit gaps/conflicts, and respect for extraction warnings. Passages
are treated as untrusted evidence, not instructions. The model must return
`needs_structured_data` for exact current assignment due dates, points,
submission state, or confirmed meeting times. Those facts remain the
responsibility of the existing structured Canvas/backend services. Template
origins, other-course origins, and ineligible chunks are excluded from context.

The model returns `status`, `blocks` (text plus `source_ids`), and `limitations`.
The backend validates the shape and rejects uncited answered blocks, invented
or undeclared source IDs, malformed citation labels, or generated URLs. Redundant
valid inline labels are removed after checking them against declared IDs. The backend renders
citations and resolves titles, URLs, and page/slide/block/cell locations from
stored metadata. `sources` contains only cited origins; `retrieved_sources`
preserves the candidate-source list for inspection. Each duplicate passage is
sent once while its original valid references remain attached.

Structured-data deferrals discard model-written limitations so document deadlines
or point values cannot leak through those explanations; they use a fixed message.
Missing indexes return `indexing_needed`; no usable context returns
`insufficient_context`; neither calls the answer provider. A partial index is
reported. With related but insufficient passages, the model is instructed to
abstain. Refusals and provider failures are distinct from insufficient evidence.
Transient errors retry with bounds; invalid output is rejected rather than
silently repaired. Raw provider bodies and credentials are not logged.

Citation validation verifies identities, not whether every statement is true.
There is no semantic verifier, reranker, or calibrated similarity threshold.
Fake-provider tests verify the request/response plumbing; separate real-model
checks assess support and abstention behavior. Real answers must still be
reviewed, particularly when source extraction is partial.

## Supported content and remaining limits

### Visual-aware file processing

Canvas files now pass through `visual_document_service.extract_file()` after
download. It reuses `document_service` for native text, then inspects PDF objects
with `pypdfium2`. Syllabus discovery/download logic is unchanged. The assistant
never initiates visual extraction or downloads during a question.

`visual_inspection.py` owns tunable `InspectionSettings`: native text length,
image count/bounds/union area, tiny repeated marginal graphics, vector content,
mathematical layout with drawings, and suspicious text. Nonempty text is not
proof that a page is complete. Unknown images favor enrichment. Only tiny images
repeated in a margin on several text-rich pages qualify as likely decoration.
This is a conservative heuristic, not a guarantee of semantic completeness.

Rendering uses full-page PNGs in memory at up to 180 DPI, bounded by a 3,200-pixel
side and 10 million pixels. `vision_service.py` provides a replaceable
`VisionExtractionProvider` and a configurable OpenAI Responses implementation.
It requests faithful ordered text/table/code/equation/diagram extraction, never
exercise solutions, and validates completion, element types and unreadable
details. It uses the existing `OPENAI_API_KEY`, `store=false`, bounded SDK retries,
and safe error categories. Existing printed solutions can be transcribed.

Paid processing is explicitly enabled in the root `.env`:

```dotenv
EASYCANVAS_VISION_ENABLED=true
EASYCANVAS_VISION_MODEL=gpt-6-luna
```

The default is inspection-only. Turning paid processing off does not prevent
reuse of already-cached compatible visual extractions. A missing key/provider
failure preserves native text and an incomplete status; successful pages are
cached immediately so interrupted files can resume without paying for them again.

The additive `visual_extractions` SQLite table keys results by account namespace,
course ID, original content hash, unit kind/number and extraction configuration
hash. That hash covers model, actual prompt/version, render settings/library,
native reference text and rendered pixels. Converter timestamps alone do not
cause paid re-extraction. Changing inspection thresholds can rerun inspection
without invalidating an otherwise identical visual transcription. Cache values
retain ordered provider elements, unreadable details, model configuration and
usage. They remain independent of derived chunks and embedding models.

Complete page transcriptions replace the page's searchable text rather than
blindly appending duplicate native paragraphs. `native_text`, `visual_elements`,
`inspection`, `extraction_method`, model/version and warnings remain stored on
the source unit. Existing document `ok`/`partial`/`no_text` status values remain;
unit `extraction_status` distinguishes `native_ok`, `vision_enriched`, `blank`,
`vision_pending`, `vision_failed` and unresolved partial extraction. A page can
remain partial even after a successful provider call if content is cut off or
unreadable. An actually blank page does not make a readable PDF incomplete.

PowerPoint rendering reuses the isolated LibreOffice converter with hidden-slide
export enabled. Page/slide counts must match before mapping; original slide
identity and speaker notes remain. Word uses explicitly labelled rendered-page
locations while preserving original native blocks; it does not invent a mapping
between paragraphs and page numbers. A conversion that loses native words cannot
replace good original text. Images, equations, text boxes and supported content
controls can be read through the rendered path, but clipped/blurred content,
conversion errors, tracked changes and legacy structure uncertainty remain visible.
OOXML rendering rejects external resource relationships (passive hyperlinks are
allowed). Inspection-only processing keeps existing native Word/slide text and
speaker notes when no compatible visual extraction is available.

Chunk locations retain page/slide/rendered-page identity, extraction method and
compact element types; full source/Canvas identity, original filename, content
hash and URLs remain attached to origins. Full native/provider text is not copied
into every chunk's location metadata. Legacy saved documents without rendered
pages keep their existing chunk fingerprints.

With the backend stopped, backfill all saved current courses, then index only
new/changed eligible text:

```bash
.venv/bin/python -m backend.app.ingestion_service --all-current --enrich-saved
.venv/bin/python -m backend.app.embedding_service --all-current
```

For one saved course use its course ID instead of `--all-current`. Repeat
`--document-key file:YOUR_FILE_ID` to limit a diagnostic backfill. No course/file
IDs are hardcoded in services. Backfill verifies individual Canvas metadata and
access; it does not rediscover all sections or change syllabus records. Unchanged
completed processing is skipped. Normal enabled ingestion uses the same path.

Explicit document names resolve against actual current filenames before semantic
search. Normalization preserves distinguishing numbers, case/separator changes
and common camel-case formatting. An explicit extension distinguishes same-stem
PDF/TXT files; exact names beat numbered copies. A course code alone does not
select assignment filenames. Named questions and applicable follow-ups are
restricted to resolved document origins; course-wide questions retain normal
search. Explicit page/slide/question references and a few rare-term matches inside
the resolved file supplement semantic results; they cannot introduce another
course/file. Full-document requests have a bounded 24-passage/32,000-character context
and expose coverage limits when incomplete. General retrieval remains bounded to
eight passages/16,000 characters. Unreadable or unindexed named files do not fall
back silently to unrelated documents. Insufficient-context model responses discard
contradictory answer blocks and return the normal safe abstention; answered
responses still require valid supplied citations.

Automated visual tests use generated PDF objects and fake providers. Real
LibreOffice rendering/conversion tests remain opt-in:

```bash
EASYCANVAS_TEST_LEGACY_CONVERSION=1 .venv/bin/python -m unittest discover -s backend/tests
```

### Native reader coverage

- Native Canvas syllabus, assignment instructions, page bodies, quiz descriptions,
  announcement messages, and discussion-topic messages are read when accessible.
- PDF text, HTML, UTF-8/UTF-16 TXT/MD/CSV files, DOC/DOCX, PPT/PPTX, and version-4 Jupyter
  notebooks are supported. Other text files can also be read when Canvas supplies
  a text MIME type, such as the C source files found in CSE 3320.
- The native PDF reader extracts text layers; the visual file-processing layer
  above distinguishes blank pages from unread visual content and enriches flagged
  pages when enabled. Calling the native reader directly does not call Vision.
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
OCR and a frontend AI assistant are not implemented by this ingestion step.

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

## Direct UTA syllabus documents

Native Canvas `syllabus_body` can be an instructor-facing wrapper whose actual
syllabus is a direct PDF/DOCX link on
`oit-ead-canvas-syllabus.s3.amazonaws.com`. Discovery now recognizes those links
only when supplied by native syllabus HTML and scoped to the configured Canvas
hostname and current course ID. It does not crawl other external websites or LTI
tools. Repeated links retain all known API-record references.

`syllabus_service.py` downloads these documents with a credential-free session,
HTTPS, no redirects, timeouts, a 50 MiB stream limit, and document-signature checks.
The existing PDF/DOCX extractors preserve pages/Word blocks. ETag/Last-Modified
conditional GETs reuse unchanged text, chunks, and embeddings. A source without
validators must be downloaded to compare its byte hash. Failures preserve old
text on disk. Temporary refresh failures keep valid cached content searchable;
confirmed loss of access, removal, invalid content or known changes still block it.

Documents use `syllabus_file:<URL SHA-256>` identity within the existing account /
course boundary and retain the native syllabus record/page and download URL in
`references`. `source_role: course_syllabus` identifies actual syllabus evidence;
it does not change similarity ranking or override Canvas structured facts.
If the same bytes already exist in a current compatible course document, a small
URL alias points to its `canonical_document_key` and adds syllabus provenance to
that document. The alias has no readable text/chunks. A newly seen URL still
needs one download to establish byte equality. Changed alias content becomes an
independent document; a removed canonical target requires a full fresh download.

Native instructor-template records remain stored and policy-ineligible. Their
quality exclusion is not inherited by the downloadable course syllabus.
Normal Canvas ingestion now queues incremental embeddings automatically when
enabled in Settings. The explicit embedding command remains a developer option;
ordinary app use does not require it. See README for readiness, retry and usage controls.

## Page continuation quality and overview context

`document_quality_service.py` reassesses page-boundary warnings using adjacent
source-page numbers, readable text, and conservative continuation signals. A
quoted end-of-page warning or explicit page-break note can be resolved when its
continuation is present on the next page. Blurred, unreadable, unknown, or missing
regions retain their warnings. Resolved notes remain in
`resolved_continuation_notes`; original text and raw Vision cache results remain
unchanged. `continues_on` / `continues_from` record the neighboring source units.
The document becomes `ok` only when its remaining page statuses and document
warnings permit it. A page break alone adds no limitation message.

Quality checks run when processing/saving documents. Existing saved records can
be reassessed locally, without downloading files or calling a model:

```bash
python3 -m backend.app.document_quality_service --all-current
# Or supply one course ID instead of --all-current.
```

This account/course-scoped operation skips failed, stale, and removed sources,
preserves the latest attempt, and refreshes only changed chunk metadata. Exact
text hashes stay the same, so existing embeddings remain reusable.

`retrieval_context.py` detects overviews of resolved documents separately from
focused question/part/function requests. Overviews read the current eligible,
indexed material directly in document order; no question embedding is needed.
All material is included when it fits within 24 passages / 32,000 characters.
Larger documents use distributed beginning/middle/end coverage, with neighboring
continuations grouped and final passages ordered. Focused requests retain cosine
search and the eight-passage / 16,000-character limit, reserving space for bounded
same-document continuation neighbors before filling with other candidates.

Context limits and extraction failures are separate: limited overviews report
`This overview covers selected sections of the document.` True unreadable content
reports `Some content could not be read reliably.` Missing embedding coverage is
reported separately and is never described as an extraction failure.
