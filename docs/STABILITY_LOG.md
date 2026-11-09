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
