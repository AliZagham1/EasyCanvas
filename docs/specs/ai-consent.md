# Global AI consent

One **AI Data Processing Consent** covers all current and future Canvas courses. It controls only external AI processing.
Canvas Sync, Dashboard, Upcoming, Priorities, Courses, Class Schedule and the rest of EasyCanvas work without it.

## Identity (honest scope)

EasyCanvas has no user login. It runs on one local Canvas token from `.env`; the "account" is the SHA-256 namespace of
Canvas URL + token (`canvas_service.get_cache_namespace`). Consent is stored per that namespace: one decision per
configured Canvas account on this installation. This is a single-user local app, not a multi-user security model, and
anyone who can reach the backend on localhost can change the setting.

## Rules

- Unknown means denied: no record, a corrupt record, an older disclosure version, an unreadable database, or no store.
- The record lives in the existing `app_settings` JSON (`ai_consent`: `status`, `version`, `decided_at`), is written only
  by `PUT /api/ai-consent`, and survives refreshes and restarts. The browser stores nothing.
- `consent_service.CONSENT_VERSION` must be bumped when the disclosure changes materially; students are then asked again.
  The modal wording lives in `frontend/app/components/ai-consent-modal.tsx`.
- Existing installations are not grandfathered, even with a saved key and indexed documents.

## What needs consent (all enforced server-side)

| Operation | Boundary |
|---|---|
| Answer generation, general answers, streaming | `OpenAIAnswerProvider.__init__`, `generate`, `stream_generate` (rechecked about every 0.5 s while streaming) |
| Query understanding | `OpenAIAnswerProvider.rewrite_query` |
| Embeddings (indexing and query-time) | `OpenAIEmbeddingProvider.__init__` and `embed_texts` (and on each retry) |
| Vision / OCR | `OpenAIVisionExtractionProvider.__init__` and `extract_page`; `vision_enabled()` is false without consent |
| Background indexing | `queue_course`, `recover_jobs` and `process_jobs` queue nothing and pause jobs without consent |
| OpenAI key verification | `PUT /api/openai-key` |
| Routes | `/assistant`, `/assistant/stream`, `POST .../search-index` return 403 `{"code": "ai_consent_required"}` before any work |

`AIConsentRequired` is not a `ValueError` or `LLMProviderError`, so the fallback and retry handlers cannot swallow it.
The stream reports it as an `error` event with the same `code`. The UI shows the disabled state, not a retrieval error.

## What does not need consent

Canvas API requests, downloads, local text extraction, local LibreOffice conversion, chunking, SQLite storage,
priorities, schedule, and reading saved conversations. Sync still queues no AI work while consent is absent, so a sync
makes no OpenAI call.

## Granting, declining, withdrawing

- Granting queues only chunks that still lack embeddings (`queue_course` compares `text_hash`), if automatic updates are on, and wakes the worker.
  Documents processed without vision are re-read by the next sync once vision is enabled and consent exists
  (the pipeline version differs); that was not verified end to end against a real course.
- Withdrawing stops new external calls at the next safe boundary: the next provider call, retry, stream event, or embedding batch.
  Batches already saved are kept; paused jobs resume after re-enabling. A request already on the wire cannot be recalled.
- Withdrawal does **not** delete anything locally: documents, extracted text, chunks, embeddings, vision extractions,
  conversations and usage records stay. Saved conversations remain readable. Use Settings -> Course storage to delete
  course materials. Withdrawal also cannot delete or recall data held by OpenAI.

## Tests

`backend/tests/test_ai_consent.py`, `frontend/tests/ai-consent.test.mjs`, `frontend/tests/browser/ai-consent.spec.ts`.
All other backend test modules import `backend/tests/ai_consent_granted.py`, which makes consent "granted" for the test
process (they use fake providers). `test_ai_consent.py` restores the real check. No test makes a paid call.

## Remaining limitations

- No authentication: the backend trusts localhost.
- The OpenAI key is stored unencrypted (existing behavior).
- Old conversations and AI-derived local data persist after withdrawal.
- An answer already generated before withdrawal stays in conversation history.
