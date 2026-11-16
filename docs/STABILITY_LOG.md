# EasyCanvas stability log

Check this log before fixing a production/demo regression. Match the symptom to an existing issue and investigate the failed invariant before adding another patch. Mark Fixed only after a real replay and a regression test; a passing build alone is insufficient. This log contains no private course text, credentials, or conversation IDs.

## EC-009 — New saved material is not searchable

Previously logged as EC-001; renamed to the requested EC-009 identifier. This is the same issue, not a second fix.

- Date: 2026-11-03
- Status: Fixed
- Symptom: Study Assistant reports indexing needed for an assignment that is already downloaded and readable.
- Root cause: automatic sync stopped after chunking; embeddings were generated only by an explicit command. Four newly saved inputs had no current embeddings while the rest of the course remained indexed.
- Layer: ingestion-to-indexing handoff and readiness reporting.
- Required invariant: every successful sync queues missing eligible inputs when automatic updates are enabled; cached vectors are reused; readiness reflects current inputs only.
- Fix: persistent indexing jobs in the existing worker, default-ON saved preference, exact course/document counts, pause/retry controls, and local API usage estimates with an optional monthly warning. Startup sync preserves the previous course list until fresh discovery finishes; recovery processes existing saved work first. Normal search still never silently indexes documents.
- Backend files: `search_index_service.py`, `api_usage_service.py`, `ingestion_store.py`, `ingestion_service.py`, `course_sync_service.py`, `embedding_service.py`, `main.py`; provider accounting in `llm_service.py`, `vision_service.py`, `visual_document_service.py`, `retrieval_service.py`, `rag_service.py`, and `conversation_service.py`. Retrieval/routing/policy algorithms are unchanged.
- Frontend files: Study Assistant and Settings pages, `course-search-status.tsx`, `search-settings.tsx`, `lib/search-index.ts`, `lib/assistant.ts`, and shared styles. No dashboard banner was added.
- Regression tests: `backend/tests/test_search_index.py` covers sync-to-search, unchanged and changed documents, exact partial counts, isolated jobs/settings, partial failures/resume, startup course-list retention, OFF/manual operation, pause during indexing, duplicate inputs, and model configuration changes. `test_api_usage.py` verifies categories, response-ID deduplication, cached-token pricing, unknown usage/rates, namespace isolation, provider boundaries including streaming/query understanding, and preservation through cleanup. Existing provider unit fixtures explicitly disable real accounting; dedicated tests use temporary SQLite. Frontend unit/browser tests cover counts, controls, persistence, usage labels and responsive layouts.
- Verification: 607 backend tests passed including real legacy Office conversion tests; 229 offline evaluation checks passed; 29 frontend unit tests and 29 browser tests passed; ESLint and the webpack production build passed. Rendered Settings was inspected at desktop/mobile sizes and against the live backend.
- Real replay: normal backend startup/sync generated only 30 missing unique inputs across four courses; unchanged cache entries remained in use. All six saved current courses reached complete eligible coverage. The affected file moved from 0/4 to 4/4 indexed. The original Specification Homework question returned HTTP 200, `answered`, `assignment_material`, with assignment instructions and all four file passages cited and no warnings. No terminal embedding command was run.
- Recorded indexing usage: 4 requests, 5,895 input tokens, approximately USD 0.0001179 using dated published standard rates. Estimates are local, begin with this feature, and are not OpenAI billing. Unknown costs remain explicitly unpriced; the monthly warning does not block paid calls.
- Restart/unchanged-sync replay: a subsequent normal backend restart and completed six-course sync left embedding usage unchanged at four requests. No cached document inputs were re-embedded.
- Live UI replay: resent the original question in its existing saved conversation. Real streaming finished with `answered`, `assignment_material`, `ready`, five valid source IDs covering the assignment plus four file passages, and no warnings. Earlier failed replies stayed in history; the fresh turn succeeded.
- Settings-polish verification (2026-11-03, before UI edits): used an isolated copy with only the affected file/extraction/chunks/current four vectors removed. Normal startup sync downloaded one file (4,842 bytes), extracted it, reused 125 other document records and 115 other chunk sets, then generated four embeddings and reused 1,199 current inputs. The original HTTP question was answered with assignment/file citations and no warnings. No terminal embedding command was used. A later restart completed all six course syncs with embedding usage unchanged.
- OFF-to-ON UI verification: in the isolated fixture, four inputs were pending while automatic indexing was OFF. The real Settings switch persisted ON and immediately started background indexing: 1,217/1,221 source passages became 1,221/1,221. The small batch completed before the next status poll; the queued/updating state is covered by the browser regression. The setting and test warning survived a backend restart. The production database was not used to manufacture missing inputs.
- Related: EC-002 (similar insufficient-answer symptom, different cause).

## EC-002 — Attendance policy resolves to an empty grade item

- Date: 2026-11-03
- Status: Fixed
- Symptom: a saved syllabus exists but attendance questions return insufficient material.
- Root cause: query resolution chose an empty native attendance grade item instead of the readable course syllabus.
- Layer: retrieval routing.
- Fix: course-policy requests prefer actual readable syllabus documents, with explicit source/alias fallback; structured facts retain their own route.
- Files: `backend/app/retrieval_router.py` and policy-routing regression tests.
- Verification: real attendance answer was replayed with syllabus citations; backend/evaluation tests passed before EC-009 work.
- Related: EC-009. Similar UI wording does not prove an indexing failure.

## EC-003 — Standalone concept varies by previous course topic

- Date: 2026-11-03
- Status: Fixed
- Symptom: the same standalone concept routes to general knowledge in one conversation and course retrieval in another.
- Root cause: query-understanding classification variance inserted a course reference not present in the user's actual question.
- Layer: post-understanding routing safeguard.
- Fix: narrow standalone-topic guard ignores incidental model-added references; explicit course requests and dependent follow-ups stay grounded. General knowledge is never a fallback for failed retrieval.
- Files: `backend/app/retrieval_router.py`, `llm_service.py`, and `backend/tests/test_general_concept_route.py`.
- Verification: saved misclassified plan replayed successfully; full backend and 229 offline evaluation checks passed at that checkpoint.
- Related: EC-002 (routing, but a different invariant).

## EC-004 — A question's subpart falls across a page boundary

- Date: 2026-11-03
- Status: Fixed
- Symptom: a correctly resolved question/subpart returns insufficient context.
- Root cause: retrieval chose the question-heading page and a later summary, omitting the next page containing the subpart. Conversation state had retained the right assignment/question/part.
- Layer: section-aware context selection.
- Fix: bounded ordered question/section expansion precedes supporting material, stops at the next peer heading, and preserves policy evidence.
- Files: `backend/app/retrieval_context.py`, `retrieval_service.py`, document-overview and conversation-intelligence tests.
- Verification: real three-turn homework replay included the next-page subpart; 564 backend tests including legacy conversions and 229 evaluation checks passed at that checkpoint.
- Related: EC-009 (insufficient-answer symptom, not the same cause).


## EC-011 — Settings search status and API usage presentation

- Date: 2026-11-03
- Status: Fixed
- Symptom: Settings is text-heavy, course readiness is hard to scan, and local cost totals can be mistaken for the complete OpenAI account bill.
- Root cause: equal-weight lifetime/day/month metrics were stacked without a clear main period or tracking-start label. Course rows used full Canvas codes, and the automatic-update checkbox lacked a clear switch affordance. Existing indexing tests protected backend correctness, not UI hierarchy or monthly reporting semantics.
- Layer: frontend presentation and usage-summary reporting.
- Fix: compact accessible switch and course readiness rows, actual indexing progress, a monthly tracked-usage panel, explicit tracking date/account-billing disclaimer, and monthly feature aggregates from the existing usage ledger. No indexing, ingestion, or provider-recording rewrite. Positive warnings show a real percentage; unset/zero warnings do not invent one, and warnings never block API calls. Tiny nonzero costs retain precision instead of appearing free.
- Files: `frontend/app/components/search-settings.tsx`, `frontend/lib/search-index.ts`, `frontend/app/globals.css`, `backend/app/api_usage_service.py`, related tests, README.
- Regression tests: `test_search_index.py` verifies OFF-to-ON immediate queuing without a Canvas sync even when the warning is reached. `test_api_usage.py` verifies monthly/lifetime separation. `frontend/tests/search-index.test.mjs` and `tests/browser/search-index.spec.ts` cover course progress/retries, warning persistence and positive/zero/unset denominators, accurate local-only labeling, responsive layouts, and rollback on save failure.
- Real replay before UI edits: in an isolated copy, removed only the Specification file's saved document/chunks/current embeddings, then used the normal startup Canvas sync. One file downloaded and extracted, four chunks created, four inputs automatically embedded, 1,199 existing inputs reused. The original assistant question returned `answered` with assignment/file citations and no warnings. The original working database was not altered by the fixture.
- Verification: 49 targeted backend indexing/usage/sync tests, 31 frontend unit tests and 34 browser tests passed. ESLint, TypeScript/webpack production build and whitespace checks passed. Desktop (1280px), intermediate (768px) and mobile (390px) renders were inspected. No regressions found in the checks run; this pass did not rerun the entire backend/evaluation suite.
- Manual checks: real isolated OFF-to-ON indexed all four pending inputs without waiting for scheduled sync; warning $10 persisted through reload/restart; an unset warning showed no percentage/progress bar. Regression data verified $3.42/$10 = 34.2%. The normal running frontend showed $0.0017 tracked this month, tracking began Nov 3, 2026, monthly feature totals, and the authoritative-account-total disclaimer, with no page errors or horizontal overflow.
- Design compliance: retained the existing neutral surfaces, Inter typography, restrained violet, subtle borders, compact hierarchy, keyboard focus and responsive structure. Native switch/progress treatments are the only new control patterns. Dashboard/sidebar and course-storage behavior were outside this presentation change.
- Related: EC-009. Presentation polish does not replace the indexing invariant.

## EC-012 — Saved course material unavailable while a Canvas sync is unfinished

- Date: 2026-11-09
- Status: **Fixed** (2026-11-09). All invariants were verified live; the duplicated-notice defect found in the final live run was corrected and covered by a reproducing regression test (approved; no further paid run). History of this entry is kept below.
- Symptom: during every course sync, document, policy and assignment-material questions were refused with "This course's saved material is being refreshed. Please retry shortly." An unexpected exception during a sync could leave a course in that state, with most of its documents hidden, until a later sync succeeded.
- Root cause: `ingest_course()` published the new snapshot (new `synced_at`, `ingestion_status: running`) before processing anything. `load_documents()` showed only documents stamped with the current `synced_at`, so documents not yet reprocessed disappeared, and the router blocked document routes while `running`. Only `RequestException`/`ValueError` were caught per file, and `visual_document_service` re-raises unexpected errors, so one defective file aborted the course and left it stuck. A changed document's old chunks stopped matching as soon as its new text was saved, which also left a retrieval gap until re-embedding.
- Why earlier invariants did not prevent it: this was a documented limitation ("Atomic sync generations are not implemented"), not a regression. EC-009 preserved the course list during startup discovery; the document-level equivalent was never implemented. Tests only exercised completed syncs. A read-only check of the 2026-11-05 data found no stuck course; the last sweep refused document questions for each course for 10–24 s.
- Layer: ingestion sync-generation visibility, per-document failure isolation, cache preservation and retrieval view.
- Required invariants: the published generation stays visible until a sync commits; unfinished syncs never hide previously usable material; confirmed Canvas access loss or removal always hides material; a changed document keeps its previous searchable version until the replacement's chunks and embeddings are ready; live Canvas facts are never replaced by saved data.
- Fix:
  - A `refresh` marker on the published snapshot. Visibility covers the published plus uncommitted generations; the commit happens only on success. Failed/interrupted markers keep material visible, and the worker recovers markers left by killed processes.
  - Per-document `processing_error` isolation in native, file and syllabus processing.
  - Preservation of the last good text for processing/network failures, including a changed file that could not be processed (`newer_version_unavailable`). Hard blocks remain for `access_revoked`, `removed`, `access_unavailable`, `superseded` and `invalid_content`.
  - Per-document chunking during sync, plus a `document_served_versions` copy of the outgoing ready version, served via `served=True` retrieval readers until the replacement is ready and then pruned.
  - The router no longer refuses during a sync. Saved-material routes add a `material_notice`; cited previous or stale sources add their own warning. Live fact routes are unchanged.
- Decisions approved for this fix (2026-11-09): document questions answer during sync, with a notice; the last good content is preserved on unexpected processing failure, but confirmed revocation/unavailability is respected; per-document rechunking with no retrieval gap.
- Behavior changes:
  - `test_cached_fallback` previously required changed-version timeouts and parser/TLS failures to hide old text; it now requires them to keep the text, marked not current. `MissingBody` (Canvas unavailable) still blocks.
  - The two refresh-refusal routing tests now require answers with notices, plus a new test that live deadlines stay live during a sync.
- Backend files: `ingestion_store.py`, `ingestion_service.py`, `chunking_service.py`, `syllabus_service.py`, `course_sync_service.py`, `search_index_service.py`, `retrieval_service.py`, `retrieval_router.py`, `rag_service.py`, `document_quality_service.py`, `conversation_service.py`, `query_understanding_service.py`. Frontend (discovery-phase correction): `app/components/assistant-message.tsx`, `lib/assistant.ts`, `app/globals.css`. No chunker, embedding, extraction-pipeline or Vision configuration changes, so no caches are invalidated.
- Regression tests: `backend/tests/test_sync_generations.py` (14 tests at first; 17 after the discovery-phase correction): running-sync visibility and search; excluded older generations; unchanged sync with no download, extraction, rechunking or embedding; previous version served until the replacement is embedded, then pruned; previous version never served after 403; 404 during a running sync hidden immediately; unexpected extractor error isolated with last good text kept and retried next sync; first-seen file error; course-level failure keeps the published generation, queues indexing and commits next sync; removed document stays until commit; interruption recorded; worker recovery of stale markers; Study Assistant answers during sync with a notice and only a question embedding; served view on an unmigrated read-only database. Updated: `test_cached_fallback.py`, `test_query_routing.py`, `test_document_overviews.py`.
- Verification (isolated, mocked Canvas/OpenAI, dead outbound proxy):
  - Before the change: 609 backend tests and 229 offline evaluation checks passed.
  - After: 627 backend tests passed, including real LibreOffice conversion.
  - Against the unmodified code, the first 13 new tests all fail: 9 on behaviour assertions and 4 because the served view/table does not exist there. The 14th test covers an edge case specific to this fix.
  - 231/231 offline evaluation checks passed, with production files unchanged.
  - Frontend: 31 unit and 34 browser tests passed; ESLint and the production build passed.
  - A read-only comparison on a consistent backup of the real database (6 courses, 430 current documents) showed identical document usability, chunk sets and search inputs between old and new code. The original database was not opened for writing.
- Real replay (2026-11-09, worktree copy of the real database, real Canvas and OpenAI, `EASYCANVAS_VISION_ENABLED=false`, original database never opened for writing):
  - Sync 1 (startup), sync 2 (manual) and sync 3 (startup, with a test-only 45 s pause after CSE 3320's marker, injected by a scratch wrapper and not app code). All 3 completed for all 6 courses with 0 failures, and every marker cleared at commit.
  - Searchability, sampled every ~2 s (read-only): in no sync did any course's searchable indexed passages or usable documents drop below the pre-sync count, including every sample taken while its marker was running (CSE 3320 held 566 passages in all 11 samples of the paused run).
  - Reuse: sync 1 found 23 new items (5 assignments, 4 announcements, 12 files, 1 quiz, 1 discussion). It built only their 20 chunk sets and 131 vectors (7 embedding calls).
  - Sync 1 also made 2 downloads of existing files whose Canvas timestamp changed. Their bytes were identical, so they were reused with no re-extraction, rechunking or re-embedding.
  - Syncs 2 and 3 downloaded, processed, chunked and embedded nothing. No Vision calls were made; all 2,773 Vision transcriptions were kept. Canvas removed nothing, so removal was not exercised live; it is covered by regression tests.
  - Question during the marker phase (sync 3): asked in CSE 3320 to use one named saved slide file and give FCFS advantages/disadvantages. It returned `answered`, `document_material`, citing slides 5, 6 and 11 of that file in that course. The warning read: "Canvas sync is in progress. This answer uses course material saved by the last completed sync." Only the question was embedded.
  - Question during the discovery phase (sync 2, same question): `answered` with correct citations, but **no notice**; see "Discovery-phase notice" below.
  - The standalone question "What are the advantages and disadvantages of FCFS scheduling?" (sync 1) was answered from general knowledge without any indication. That is not an EC-012 issue; it is logged as EC-013.
  - Mocked failure replay on a scratch copy of the real data, with Canvas/syllabus/OpenAI mocked and all network blocked (0 requests attempted), passed all 11 checks:
    - An interruption mid-file-loop kept the published generation, marked `interrupted`, lost no searchable passages, and the next sync committed with 0 downloads/reprocessing.
    - A changed file whose extractor raised continued the course and kept its last good text (`stale_but_usable`, `newer_version_unavailable`); all 566 passages stayed.
    - A file with 403 was hidden immediately during the running sync and after commit, with every other document unchanged.
  - Recorded API usage for the whole replay: 13 calls, all priced, no Vision. 7 embedding calls ($0.00067), 3 query-understanding calls ($0.00058) and 3 answer calls ($0.00142): about USD 0.0027 in total (local estimate, not OpenAI billing).
  - Pending work, not an EC-012 failure: 7 newly discovered PDF/Office files were read with native text only and are pending Vision enrichment, because Vision was disabled for the replay. Only their pages will be paid for when Vision is next enabled (`--enrich-saved` or a normal enabled sync).
- Discovery-phase notice (found in the live replay, corrected 2026-11-09):
  - Root cause: the marker was written only after `sync_course()` returned, because it carried the new generation's `synced_at`. Canvas discovery took 9–15 s of each course's sync window (about half). Questions routed in that phase used the untouched published material correctly but without a notice.
  - Correction: `_begin_refresh()` now writes `{status: running, phase: discovery, synced_at: null}` before discovery. `_start_processing()` sets `phase: processing` and the new `synced_at` afterwards. A discovery failure now marks the refresh `failed` (published material unchanged), and a first-sync discovery failure saves nothing. Visibility is unchanged during discovery, because no document is restamped until processing.
  - Frontend: the notice is shown as a subtle line under the answer, outside the collapsed "Material limitations" list (`material_notice` in the response; not repeated in the list), including restored history. Live fact routes are not labelled (tested for deadlines and assignment lists).
  - Regression tests: `test_sync_generations.py` covers the marker/notice/searchability during discovery and processing, discovery failure keeping material with the "did not finish" notice and recovering next sync, and first-sync discovery failure saving nothing. The assistant end-to-end now runs in the discovery phase and asserts `material_notice`. `test_answer_basis.py` covers a live assignment list during discovery with no notice. Frontend: unit tests for `limitationWarnings`; a browser test that the notice is visible without expanding, not duplicated, and survives reload. Against code without this correction, both discovery tests fail.
  - Remaining verification: completed in the final live verification below.
- Final live verification (2026-11-09, approved; database copy, real Canvas/OpenAI, Vision disabled):
  - One sync through a test-only wrapper (not app code) that held CSE 3320 for 40 s in each phase. All 6 courses passed through `discovery` then `processing`; the computed notice was present in both phases for every course, and every marker cleared at commit (0 failures).
  - Searchable indexed passages never dropped below the pre-sync count in either phase for any course (CSE 3320: 566 in all 12 discovery and 12 processing samples).
  - Nothing was downloaded, processed, rechunked or embedded; there were no Vision calls.
  - An explicit CSE 3320 slides question asked during discovery returned `answered`, `course_material`, citing slides 5, 6 and 11 of one saved slide file and page 34 of one saved PDF, with the discovery-phase notice.
  - A course-material follow-up asked during processing returned `answered`, `document_material` (the active slide file), with the notice.
  - The real frontend rendered the saved responses (no assistant requests): the notice appears as a visible line under cited answers. Live fact routes were not exercised live; they are covered by regression tests.
  - Usage for the whole final run (shared with EC-013): 12 calls, all priced, no Vision. 4 question-only embeddings ($0.000002), 4 query-understanding calls ($0.00081) and 4 answer calls ($0.00187): about USD 0.0027.
- Defect found in the final run, corrected 2026-11-09: duplicated sync notice.
  - Symptom: the processing-phase answer listed the notice twice. Once as `material_notice` (shown correctly), and once as a model-written limitation ("Canvas sync is in progress; this answer uses course material…") inside "Material limitations". That copy differs by one character, so the exact-match de-duplication missed it.
  - Root cause (confirmed offline): `rag_service._prepare_context()` adds `material_notice` to `warnings` before copying them into each passage's `limitations`, so the answer model is told about the sync and may echo it.
  - Correction: `_prepare_context()` now builds passage limitations from the warnings minus `material_notice`. The notice is added only by the backend to the response (`material_notice` and warnings), so the UI shows it once, as the visible line. Index and extraction limitations still reach the model and the UI; answer generation and retrieval are otherwise unchanged.
  - Regression test: `test_sync_generations.py::test_sync_notice_is_not_sent_to_the_answer_model_and_appears_once`. Its fake model echoes any sync wording with different punctuation, as the live model did, and the course index is partial.
    - Passage limitations sent to the model contain no sync wording but still contain the incomplete-index limitation.
    - The final response has exactly one sync warning (the `material_notice`), plus the index limitation, the model's own limitation, and citations.
    - The test fails on the code without this correction and passes with it.
  - Verification: 649 backend tests (also with real LibreOffice conversion), 231/231 offline evaluation, 33 frontend unit and 35 browser tests, ESLint and production build passed.
- Remaining risks: the notice reflects the course being refreshed, not courses still queued in the same sweep, whose material is unchanged. A course whose discovery keeps failing carries the "did not finish" notice until a sync succeeds. A document removed from Canvas stays visible until the next successful commit (at most one sync, or longer while syncs keep failing). With automatic indexing OFF, the previous version keeps serving until Update course search. A chunker-version change still has no previous copy. SQLite write contention between the web and worker processes is unchanged (separate issue).
- Related: EC-009 (course-list retention during startup sync, the same invariant one level up), EC-002/EC-004 (similar "insufficient answer" wording, different causes).

## EC-013 — General-knowledge answers are not identified; "my slides" requests can report a missing document

- Date: 2026-11-09
- Status: **Fixed** (2026-11-09, after isolated regression tests and live verification; see below).
- Symptom: in the EC-012 live replay, "What are the advantages and disadvantages of FCFS scheduling?" in CSE 3320 was answered from general model knowledge, with no citations and no visible sign that course material was not used. Investigation also found that "According to my CSE 3320 slides…" can be answered "I could not find that document in this course." for some model plan shapes.
- Investigation:
  1. The live query-understanding plan was `general_concept`, `topic_switch`, with no course/assignment/document references. `_general_concept_allowed()` accepted it: the question starts with "what are" and names no course word, course code or "my/our/this" pointer.
  2. This matches the intended EC-003 design and the query prompt ("'What is recursion?' can use general knowledge"); the classification was not a bug.
  3. Selecting a course does not change this: course selection scopes retrieval but does not force concept questions through RAG. A course code, course/material wording, or a model-misclassified `general_concept` with course words routes to course retrieval (verified offline).
  4. The backend already returned and persisted `answer_basis: general_knowledge` (with `retrieval_route: general_concept` and empty sources). The frontend ignored it entirely.
  5. History can change routing in one direction only: if the model marks the same question as a continuation of active course material, it is answered from that material. General follow-ups ("give me an example") stay general only after a general answer with no active documents. No path leads from failed retrieval to general knowledge.
- Root causes:
  - Transparency: the frontend never rendered `answer_basis`, so uncited general answers looked like normal course answers.
  - Routing bug: when the model puts a generic material description ("slides", "lecture slides", "my slides", "CSE 3320 slides") into `document_reference`, `resolve_document_reference()` cannot match a file and returns `none`. `_route_query()` then returned "I could not find that document in this course." instead of searching the selected course. It was safe (no general fallback) but violated "explicit course-material requests retrieve course material".
- Layer: frontend answer presentation; one branch of document-reference routing.
- Required invariants: general-knowledge answers are visibly identified; course-grounded answers keep citations; explicit course-material requests retrieve the selected course's material; standalone concepts may use general knowledge; failed course retrieval never falls back to general knowledge.
- Fix:
  - Frontend shows a subtle "General knowledge · not from your course materials" label only for `answer_basis: general_knowledge`, including restored history. It reuses the existing backend field; no new classification.
  - Router: in the not-found branch only, a document reference made solely of generic material words plus the selected course's own code/name words runs the normal course-wide retrieval (`course_material`). Any other word or number remains a specific reference and still reports a missing document. The classifier, the general-concept guard and all other routes are unchanged.
- Files: `backend/app/retrieval_router.py` (`GENERIC_MATERIAL_WORDS`, `_generic_material_reference`, one branch); `frontend/lib/assistant.ts` (`answerBasisLabel`), `frontend/app/components/assistant-message.tsx`, `frontend/app/globals.css`; specs `docs/specs/study-assistant.md`.
- Regression tests: `backend/tests/test_answer_basis.py` (10 tests), covering:
  - a standalone concept with a selected course stays general without an embedding call;
  - seven explicit "my slides" plan shapes, including a misclassified `general_concept`, reach course material in the selected course only;
  - unmatched specific references ("Lecture 7 slides", "Kernel Internals slides", "HW 99 notes") still report a missing document;
  - a named file still resolves to that file;
  - a general answer is marked, uncited and persisted with `answer_basis`;
  - a course-grounded answer has citations and no general basis;
  - a slides follow-up after a general answer uses course material, and a general follow-up stays marked;
  - unindexed course material returns `indexing_needed` for three plan shapes, never general knowledge.

  Frontend: unit tests for `answerBasisLabel`, and a browser test for the label, no source list and persistence after reload. Against code without the routing correction, the 5 explicit plan shapes that carry a generic `document_reference` fail, as do the slides follow-up, the cited course answer and one failed-retrieval case. The 2 shapes without a document reference already worked.
- Verification: 640 unique backend tests (also with real LibreOffice conversion), 231/231 offline evaluation, 33 frontend unit and 35 browser tests, ESLint and production build all passed. Desktop and 390px renders were inspected with no horizontal overflow. Correction (2026-11-09): this was first reported as 646 because `test_answer_basis.py` imported a TestCase by name and unittest collected its 6 tests twice; the import now borrows helpers by module reference.
- Remaining verification and risks: the frequency with which the live model emits generic `document_reference` values was not measured; the paid `live-query` evaluation would. The descriptor list is English and deliberately small; an unlisted generic word still produces the (safe) missing-document notice. Whether course material should be preferred over general knowledge when both could answer a standalone concept is a product decision; this fix only labels the basis.
- Live verification (2026-11-09, approved; CSE 3320, real OpenAI and Canvas, Vision disabled):
  - An explicit "According to my CSE 3320 slides…" question got the plan `course_wide_question` with `document_reference: "course slides"`, the generic-reference shape fixed here; previously it would have reported a missing document. It was routed to `course_material` with CSE 3320 citations and no `answer_basis`.
  - A follow-up ("Can you explain that using our course materials?") was answered from course material with citations.
  - A CSE 3320 slides question on an uncovered topic (Krebs cycle) returned `insufficient_context` with no citations and no `answer_basis`, never general knowledge.
  - The real frontend rendered the real saved general-knowledge response from the first replay with the "General knowledge · not from your course materials" label and no sources, and the cited answers without the label (no assistant requests made).
  - Not observed: in this run the standalone FCFS question was not classified as general (see EC-014). The general route itself was observed live in the EC-012 replay.
  - Usage: shared with the EC-012 final run (12 calls, about USD 0.0027 in total).
- Related: EC-003 (standalone-concept routing), EC-012 (where this was observed), EC-014 (EC-003-type recurrence found during this verification).

## EC-014 — Standalone concept routed to course material in a new conversation (EC-003 recurrence)

- Date: 2026-11-09
- Status: **Fixed** (2026-11-09). Regression tests pass, and the exact plans produced by the live model were replayed through the real router against the real database copy (the same standard as EC-003). No fresh paid question was asked; see verification.
- Symptom: "What are the advantages and disadvantages of FCFS scheduling?", the first turn of a new CSE 3320 conversation, was answered from course material (`course_material`, cited a saved slide file). In the EC-012 replay, the same question in a new conversation was answered from general knowledge. The answer was correct, cited and safe, but routing is inconsistent: the EC-003 symptom.
- Root cause: the live query-understanding plan was `course_wide_question` with `operation: continue`, `use_active_documents: true` and `course_reference: "2268-CSE-3320-003-OPERATING SYSTEMS"`. The student did not write that reference, and the conversation had no previous turns or active documents to continue. The EC-003 guard (`_correct_standalone_concept`) removes model-added references only when `operation == "topic_switch"`, so a hallucinated continuation bypasses it.
- Why the EC-003 invariant and tests did not prevent it: EC-003's tests supply `topic_switch` plans; none covers a model claiming continuation in a conversation with nothing to continue.
- Impact: no unsafe output; the variance always moves toward grounded, cited material, never toward ungrounded knowledge. Students may get course-material answers for some standalone concepts and general answers for others.
- Fix (approved 2026-11-09): `conversation_state_service.reject_unsupported_continuation()` runs at the start of `interpret_turn()`, which both query understanding and the router already apply to every model plan.
  - If deterministic state shows no recent turns, active documents, active assignments, pending clarification or clarification selection, a model's `continue` becomes `topic_switch` and `use_active_documents` becomes false.
  - Other operations (for example a first-turn "other than HW1" exclusion) and all references are unchanged. The existing EC-003 guard then drops only references the student did not write; student-written course references are preserved.
  - No new classification logic was added.
- Files: `backend/app/conversation_state_service.py`.
- Regression tests: `backend/tests/test_continuation_grounding.py` (8 tests):
  - the claim is rejected only without state, and each kind of state (turns, active documents, active assignments, pending clarification) keeps it;
  - other operations are not rewritten;
  - the exact live first-turn shape becomes a labelled general answer with no sources or invented context;
  - the stateless route also rejects the claim;
  - a genuine "part b" follow-up keeps `continue`, active documents and HW 1;
  - first-turn explicit course requests ("According to my CSE 3311 slides…", "… in CSE 3311?") keep the student's reference and reach cited course material;
  - a course switch starts a new conversation, rejects the continuation claim, and cites only the new course (conversations cannot cross courses);
  - an unindexed course after a rejected claim returns `indexing_needed`, never general knowledge.

  Against the code without this correction, the first-turn, stateless, explicit-course (both questions) and course-switch tests fail; the follow-up and failed-retrieval tests pass in both, as intended.
- Verification:
  - 649 backend tests (also with real LibreOffice conversion), 231/231 offline evaluation, 33 frontend unit and 35 browser tests, ESLint and production build passed.
  - Saved-live-plan replay (read-only database copy, zero-cost stub question vector, no paid calls): the four plans recorded in the EC-013 live run went through the real router.
    - The failing standalone plan now routes to `general_concept`, with its invented course reference dropped.
    - The two first-turn "CSE 3320 slides" plans, which carried the same unsupported continuation claim, still route to `course_material` in CSE 3320 with the student's reference preserved.
    - The genuine follow-up still routes to the active file with its continuation preserved.
  - Observation: the live model made this unsupported claim on 3 of 4 first turns in that run, so the correction matters in practice.
- Remaining risk: a fresh live question cannot force the model to repeat the misclassification, so live confirmation of the student-facing answer is optional. The paid `live-query` evaluation would measure how often the shape occurs.
- Related: EC-003 (same invariant, different trigger), EC-013 (found during its live verification).
