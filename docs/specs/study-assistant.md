# Study Assistant: query understanding and source routing

Implemented backend flow:

Student question → one structured understanding call → deterministic resolution
against the selected course → source-specific retrieval → validated answer.

The existing FastAPI JSON and streaming endpoints remain the browser boundary.
No OpenAI key, Canvas authentication header, raw provider error, or arbitrary
model-chosen API URL is sent to the frontend.

## Query contract

`query_understanding_service.py` defines and validates the JSON schema used by
`llm_service.py / rewrite_query()`. It runs on first turns and follow-ups. The
provider returns reference strings, never assignment/document IDs.

Supported intents are `list_assignments`, `explain_assignment`, `explain_document`,
`explain_question`, `solve_question`, `summarize_document`, `explain_rubric`,
`get_deadline`, `get_submission_status`, `get_assignment_details`,
`find_course_material`, `course_wide_question`, and `study_plan`.

Fields include `query`, `course_reference`, `assignment_reference`,
`document_reference`, `assignment_number`, `homework_number`, `question_number`,
`part`, `deadline_reference`, `requested_data`, `requires_live_canvas`,
`use_active_documents`, and `clarification`. Number fields are strings; meaningful
assignment/document/question numbers remain separate.

The query model receives bounded real assignment/file titles and bounded recent
conversation context. Its job is to distinguish the action from modifiers: an
explanation of homework "that is due" is not a deadline lookup. Grading criteria,
grading breakdown and "how is this graded" can identify rubric intent without
the word "rubric". Schema compliance does not guarantee correct interpretation;
deterministic entity checks still control source selection.

## Resolution and routes

`document_reference_service.py` matches numbered aliases and actual assignment
titles. `retrieval_router.py` selects a route after understanding:

| Request | Source and behavior |
|---|---|
| Assignment/homework lists and exclusions | Current Canvas assignment entities, filtered by actual title/type and explicit exclusions; no document search or answer-model call. |
| Assignment explanation | Actual assignment instructions plus linked question/instruction files; solutions stay separate. |
| Rubric explanation | `snapshot.assignments[].metadata.rubric`, including criteria, rating descriptions, points, settings and snapshot time. No vector search or embedding required. |
| Deadline/submission/assignment points | Fresh individual Canvas assignment request with this student's submission information; response text comes from backend code, not an answer model. |
| Assignment identified by a date | Fresh course assignments, course-local aware date filtering, then clarification if multiple assignments match. |
| Named document overview | Existing ordered document coverage and continuation handling, within context bounds. |
| Focused question | Existing semantic retrieval inside resolved material, with bounded neighbors. |
| General course knowledge | Existing selected-course semantic retrieval. |
| Study plan | Recognized, but clearly reported as not implemented. No schedule is invented. |

The backend decides whether a route needs fresh Canvas data; the model's flag
cannot authorize arbitrary requests or substitute cached dates for current ones.
No paid document indexing or ingestion starts when a question is asked.

Module labels can identify work even when its file has a different name. The
resolver checks the selected course's current module catalog and saved file
references by module ID. A unique `Lab 4` module can supply that lab's files when
its native assignment has no description or attachment. Direct assignment links
keep priority. Numbered work types remain distinct (`Lab 4`, `Assignment 4`, and
`Lab 40`); question/instruction files and solution files remain separate.
Duplicate module labels ask for clarification. Generic containers such as
`Week 4` are not assumed to identify `Lab 4`.

Explicit filenames and follow-ups retain the file's verified module context;
they do not attach another native assignment merely because its number matches
the filename. The answer model receives current module labels with its passages
so it can explain differently named files without inventing relationships.
This reads existing course-scoped metadata; it does not redownload documents,
change extraction, or rebuild chunks/embeddings. Courses without saved module
relationships still depend on existing links/name matching or clarification.

## Citations and conversation

Structured rubric/fact evidence uses `source_kind: structured_canvas` and the
actual assignment identity and URL. It is not labelled embedding-eligible or
stored as an embedding merely to pass the RAG interface. Rubric criteria retain
their IDs, rating levels and points; those points are not an earned score.
Document evidence retains its existing eligibility, source-quality and location
checks. Answer source IDs are still validated against backend-owned evidence.

`conversation_service.py` caches bounded native-assignment and document context
and persists it with the thread. Parts, functions and rubric follow-ups use that context. A new
topic drops active focus; another account/course cannot reuse the session.
Completed turn retries reuse their cached response before making another model
call. SQLite now persists full conversations and validated replies, including
citations; only the last three turns (6,000 characters total) and compact
resolved state are supplied as conversational model context. On an explicit
topic switch, prior answers and active references are removed from answer context.

`conversation_state_service.py` retains the current intent, recent entity list,
selection, exclusions, part/question, rubric and module context. The understanding
contract includes `operation`, `entity_type`, `included_references` and
`excluded_references`. Reference strings are resolved against actual course
records; model-selected IDs are never accepted. A relational follow-up can keep
the deadline/rubric action while changing the entity. Partial titles must match
actual words, and ambiguous matches clarify. Assignment and homework are not
unconditionally equivalent aliases.

`conversation_store.py` lazily adds `assistant_conversations` and
`assistant_messages` to the existing SQLite database. The first table stores
namespace, course/conversation IDs, timestamps, title and compact state. The
second stores UUID message IDs, turn IDs, roles, content, timestamps and the
validated assistant response/source metadata. Account/course checks apply to
every history read. Completed exchanges and state are saved atomically. Failed
or interrupted turns retain the question, but never save an unvalidated draft.
No extracted documents, chunks, embeddings or Vision data are migrated.

History endpoints (all course-scoped):

- `GET /api/courses/{course_id}/conversations`: list threads, most recently active first.
- `POST /api/courses/{course_id}/conversations`: create an independent empty thread.
- `GET /api/courses/{course_id}/conversations/{conversation_id}`: restore messages and mark the thread active.
- `PATCH /api/courses/{course_id}/conversations/{conversation_id}` with `{"title":"..."}`: rename only display-title metadata.
- `DELETE /api/courses/{course_id}/conversations/{conversation_id}`: delete one thread and its dependent messages atomically.

The existing streaming endpoint accepts that thread's ID. The frontend restores
the most recently active thread when returning to a course; its Conversation
dropdown reopens older threads. New conversation creates a new persisted ID,
without deleting the previous one. No private chats are placed in localStorage.
Previously expired/in-memory-only conversations cannot be recovered retroactively.

Rename accepts trimmed single-line Unicode names of 1–100 characters. The additive
`title_is_custom INTEGER NOT NULL DEFAULT 0` migration distinguishes manual names
from automatic first-question titles. Renaming leaves the ID, course, creation
timestamp, messages/citations and compact state unchanged. Deletion validates the
account/course, removes messages before the parent row in one transaction, and
evicts only that thread's memory cache. A running response prevents deletion;
session acquisition and deletion share a process-local lock. Course documents,
chunks, embeddings and other chats are never deleted by this action. The UI asks
for confirmation, then opens another thread or creates a clean one when none remain.

Explanation prompts request useful detail by default, grounded in the supplied
requirements/parts/constraints. Beginner wording simplifies terminology rather
than just shortening the reply; explicit short-version requests remain concise.
Deadlines and lists remain direct structured responses. Prompt/provider behavior
is tested separately from source routing; fake-provider passes are not a measure
of real answer quality.

Structured assignment clarification retains the backend's ordered candidate IDs,
titles and requested action in that account/course's in-memory conversation. Short
replies such as `all`, `all of them`, `each one`, `all three`, `presentation`, and
`the second one` resolve only against that set. Deadline replies fetch every
selected assignment from Canvas and retain a separate citation per assignment.
These bounded replies need no additional understanding/answer model call. New
topics, completed choices and session expiry clear the candidate context; bare
`all` without a candidate set asks for clarification. At most 12 candidates are
retained, and removed/unavailable candidates require a fresh selection.

Document warnings remain on stored sources. Answer-level extraction notices are
scoped to cited units and the question: clean policy text does not inherit an
unrelated document-wide image warning. Visual-dependent questions, local unreadable
regions, unexplained partial content and limited overview coverage keep their
warnings. Normal ordered list continuation across pages is not unreadability.

Both endpoint responses include `query_understanding`, `retrieval_route`, and
resolved identities for verification. Structured routes also retain the
allowlisted structured data used. These fields are not rendered as technical
debugging text in the normal conversation UI. Public tests print synthetic
parsed plans and selected sources; real private evidence belongs in ignored
local reports, not committed logs.

## Assignment-policy enforcement

`assignment_policy_service.py` reads restrictions only from backend-resolved native
assignment instructions and related non-solution instruction documents. This
includes module-related/file-only work. Bounded policy/submission excerpts are
reserved ahead of competing solution passages, with original source IDs and
page/slide/block metadata where available. A solution file does not erase the
applicable question-document policy. Instructor-template sources remain excluded.

Clear AI/GenAI-generated-work bans, outside-assistance bans and individual-work
requirements are separate rule categories. Submission-format requirements are
preserved separately; they do not automatically become an AI ban. These are
deterministic English-language rules, not a complete interpreter for arbitrary
institutional policies. Permissive wording is not treated as a prohibition.

Explicit solution requests under a known assistance restriction receive a cited,
backend-written alternative offering conceptual/instruction help, without answer
generation. Other generated answers undergo independent citation/shape checks
and policy checks. Obvious solution/code leakage or contradictory submission
formats are replaced with a compliant cited response. Unknown citations still
fail validation. This does not prove arbitrary prose semantically compliant.

Policy-sensitive generated drafts are buffered until validation so prohibited
text cannot appear before rejection. Unrestricted responses retain real provider
streaming. NUL/corrupt control characters are rejected in final answers and
streamed text; Unicode, mathematical symbols, tabs and normal newlines remain valid.

## Current limits

- Rubrics reflect the saved snapshot and its timestamp; normal ingestion refreshes
  them. Current deadlines, submission states and assignment points use live API data.
- Calendar references support today, tomorrow, weekdays, this/next week, and ISO
  dates. Unsupported date expressions ask for clarification. Date filtering and
  deterministic fact text use the course's IANA timezone, or explicitly UTC when
  unavailable. The response preserves the raw timestamp for later local display.
  The `tzdata` dependency supplies timezone definitions on systems without them.
- The model sees at most 200 assignment titles and 200 file titles. Backend
  resolution still checks actual current records and never accepts invented IDs.
- Missing records, ambiguous titles and multiple assignments in a date window
  are not replaced with an unrelated vector-search result.
- During an in-progress course material refresh, document routes return a
  specific retry/indexing message instead of falsely reporting missing files.
  Saved rubric and current-fact routes remain available. Atomic sync generations
  are not implemented by this layer.
- Study planning, priority scoring, LangGraph, MCP and additional orchestration
  are not added by query routing.
- Active-stream locks remain single-worker in-memory locks. Persistent chat
  history survives restarts, but multi-worker/distributed chat locking is not
  implemented. Database storage must remain on persistent disk when deployed.

## Verification

```bash
python3 -m unittest discover -s backend/tests
EASYCANVAS_TEST_LEGACY_CONVERSION=1 python3 -m unittest discover -s backend/tests
```

Automated routing/provider tests use fake providers and a temporary SQLite store.
Live understanding and answer checks are separate, explicitly authorized tests
that consume API credit and may send private selected-course content to OpenAI.
