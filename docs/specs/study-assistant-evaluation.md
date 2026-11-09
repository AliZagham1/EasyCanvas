# Study Assistant regression and evaluation suite

This suite measures the current implementation before production fixes. It does
not change prompts, routing, extraction, ingestion, embeddings, or application
storage. Reports retain failures and return a nonzero exit code for failures or
blocked checks. Do not change expectations just to make a baseline green.

## Files and evidence types

- `backend/evaluation/prompts.json`: 40 named, synthetic student prompts with
  explicit intents, references, context, expected sources, routes and modes.
- `fixtures.py`: temporary SQLite course, fake vectors and declared provider
  replays. These never use the student's database or API key.
- `checks.py`: behavior assertions for understanding, evidence selection,
  citation membership, assignment requirements, policy responses and formatting.
- `visual_cases.py`: seven real synthetic PDFs: selectable text, scanned text,
  mixed screenshots, matrices/equations, code, tables and a labelled diagram.
- `run.py`: runner, isolated conversation checks, component probes, explicit live
  integrations and Markdown/JSON reports.
- `backend/tests/test_assistant_evaluation.py`: 12 free tests of the harness,
  dataset, checkers, live opt-in gate, source checks and context replay.

All public course IDs/content are synthetic. Private live courses are resolved
from the saved current-course list by course code, not fixed personal Canvas IDs.

**A declared intent is not evidence that a language model understood a question.**
Offline `query_contract` checks validate supplied fixtures. Actual understanding
is measured only in `live-query`. Fake answers exercise the real citation and
conversation pipeline, not the quality of real generated explanations.

## Dataset categories

The 40 prompts cover homework aliases/paraphrases, 1-versus-10 safety, deadline
actions versus due-date modifiers, named/active document overviews, focused
questions, rubric paraphrases and follow-ups, assignment policy, context changes,
submission status, unknown references and recognition of unimplemented planning.

`fixture_plan` is the declared input to deterministic routing. `expected` is an
independent assertion about behavior. Reference spellings may differ: the checker
compares actual resolved identity, not exact full natural-language wording.

The user's domain action `solve_assignment` maps to the existing production
intent `solve_question`; the suite does not introduce a production intent.

Date-only assignment explanations remain explanation intent but may need a
current Canvas lookup to identify the assignment. Named homework explanation
does not need a deadline lookup merely because it contains the word “due”.

## Free checks

From the repository root, with dependencies installed:

```bash
source .venv/bin/activate
python3 -m unittest backend.tests.test_assistant_evaluation
python3 -m backend.evaluation.run --mode offline
```

The offline evaluation contains 228 observations:

- 40 declared query-contract checks
- 40 actual routing/retrieval checks with fake vectors and structured Canvas reads
- 6 conversation/context/isolation checks with declared provider replays
- 4 actual answer-validation probes, including an unsafe policy-answer replay
- 14 extraction/visual pipeline checks
- 10 continuation, readability-warning and overview-coverage checks
- 8 clarification follow-up tests (including all/name/ordinal replies and isolation)
- 11 answer-warning relevance tests
- 9 pending-visual/cache/targeted-indexing tests
- 20 module/document matching tests, including aliases, exact numbers, role
  separation, ambiguity, direct-link priority, follow-ups, current labels,
  provenance, indexing state and account/course isolation
- 20 conversation-intelligence/persistence/depth checks
- 4 history API checks, including creation, reopening, isolation and safe failures
- 14 cached-content fallback checks covering network failures, revocation,
  invalid content, recovery, unchanged vectors and course/account isolation
- 15 policy/control-character checks, including deliberately violating fake answers
- 13 conversation rename/delete, schema-migration and management API checks

The legacy `Assignment 1` alias case now expects clarification, as required by
the product brief. Its deliberately unsafe provider hint still says `HW1`, so
the test verifies the backend refuses to silently create that equivalence.
The policy-solution evidence, unsafe-answer replay and NUL-output probes remain
unchanged and now pass through production fixes rather than relaxed expectations.

Visual probes test real detection, storage, chunking, source metadata and search
handoff with known fake transcriptions/vectors. They do **not** measure real
Vision recognition, diagram interpretation, math accuracy or semantic ranking
quality. No paid vision or document re-ingestion runs.

The full existing backend suite remains separate:

```bash
python3 -m unittest discover -s backend/tests
EASYCANVAS_TEST_LEGACY_CONVERSION=1 python3 -m unittest discover -s backend/tests
```

The second command needs LibreOffice and permission to launch the converter.
Known failing evaluation scenarios remain visible in the evaluator's report and
exit code, rather than being marked as expected failures in the unit suite.
Harness unit tests verify that bad behavior is detected; they do not certify
that the application passes the evaluation.

## Explicit paid integrations

These commands use the existing configured query/answer/embedding providers.
They require `OPENAI_API_KEY`; normal tests never run them automatically.

```bash
# 40 real query-model calls on the synthetic course; routing remains local.
python3 -m backend.evaluation.run --mode live-query --allow-paid \
  --output backend/data/reports/study-assistant-live-query

# 12 actual conversation turns on saved private courses, with fresh retrieval.
python3 -m backend.evaluation.run --mode live-answers --allow-paid \
  --output backend/data/reports/study-assistant-live-answers

# Both live suites plus all free component/context/guard checks.
python3 -m backend.evaluation.run --mode all --allow-paid \
  --output backend/data/reports/study-assistant-complete-baseline
```

Live query checks measure raw model fields and backend routing separately. For
example, a mistaken `requires_live_canvas` suggestion may fail the understanding
check even when the backend safely overrides it and the route passes.

The 12 real turns cover CSE 3311 presentation rubric/follow-ups, the four-turn
CSE 4321 HW1 conversation, CSE 3302 HW6 explanation/AI-use restriction/follow-up,
and CSE 3380 Practice Questions 10 overview/focused follow-up. CSE 3302's policy
is not confused with CSE 4321's separately named HW6.

Real checks send questions, course titles and selected passages to OpenAI and
consume credit, including question embeddings. The real store is opened in
read-only mode without constructor migrations. No document indexing, database
writes or ingestion runs. An unavailable/refreshing course or provider error is
reported as blocked, never counted as passed.

## Reports and interpretation

Default reports are `backend/data/reports/study-assistant-baseline.md` and `.json`.
This directory is Git-ignored. Do not commit private reports, extracted document
text or full real responses. `--output` can select another report prefix; keep
private output in an ignored directory.

Reports include every parsed plan, resolved identities, selected route, actual
passages/source metadata, final responses where generated, pass/fail reasons,
likely failing layer, code/dataset fingerprints and relation to recent routing
work. The runner compares production-file hashes before/after execution.

The unsafe answer replay deliberately supplies a policy-violating answer with
valid citations. Acceptance demonstrates a missing enforcement boundary, not
that a real model always violates the policy. The real policy integration tests
are needed to measure actual model behavior.

Answer-quality assertions check explicit supported facts and behavior rather
than exact prose. They are limited heuristics: they cannot prove every answer is
entailed, every equation is correct or every policy response is appropriate.
Human review of recorded answers remains necessary. Do not add an LLM judge or
weaken checks silently during baseline collection.

## Add a regression after a real failure

1. Save a synthetic reproduction with a unique name and explicit expected action.
2. Include relevant prior-turn context and expected source identities/roles.
3. Run against unchanged production behavior and preserve the failing report.
4. Classify the first failing layer; distinguish upstream failures from cascades.
5. Review and approve the production fix separately.
6. Rerun the evaluator and backend suite, keeping previous cases.

Update this dataset for broad behavior, not exact filename-only patches. For
new private cases, resolve sources from the account's records at runtime and
keep raw evidence local.
