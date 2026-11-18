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
| Generic course-material reference ("my CSE 3320 slides", "lecture notes") | Selected-course semantic retrieval (EC-013), not a missing-document notice. |
| Standalone concept (`general_concept`) | Uncited general knowledge, marked `answer_basis: general_knowledge`. |
| Study plan | Recognized, but clearly reported as not implemented. No schedule is invented. |

### General knowledge versus course material (EC-013)

A selected course does not force every concept question through RAG. The router
accepts `general_concept` only for a self-contained topic switch with no course,
assignment, document, number, deadline or "my/our/this course" wording
(`_general_concept_allowed`). A general follow-up such as "give me an example" can
stay general only after a general answer, with no active course documents.
Model-added course references that the student did not write are ignored
(EC-003). Anything else, including a misclassified `general_concept` with course
wording, is routed to course retrieval.

General knowledge is never a fallback for failed retrieval. A course route with
missing, unindexed or insufficient material returns `indexing_needed`,
`insufficient_context` or a clarification, never an uncited answer.

When the model puts a generic material description into `document_reference`,
the router searches the selected course instead of reporting "I could not find
that document". A generic description uses only descriptor words (slides,
lecture, notes, material, readings, presentation, pdf, file, course, class) plus
the selected course's own code and name words. Any other word or number ("Lecture
7 slides", a title word) is a specific reference, and an unmatched one still
reports the missing document.

A model's continuation claim (`operation: continue` or `use_active_documents`) is
accepted only when deterministic conversation state exists: previous turns, active
documents or assignments, or a pending clarification (EC-014,
`reject_unsupported_continuation`). Otherwise the plan becomes a topic switch before
the EC-003 guard, so a first-turn standalone concept is not grounded in invented
context, while student-written course references are kept.

The same standalone question can route differently after a course-material turn.
If the model treats it as a continuation (`use_active_documents`), it is answered
from the active course material. This is deliberate: the deviation always goes
toward grounded material, never toward ungrounded knowledge.

The frontend shows a subtle "General knowledge · not from your course materials"
label for responses with `answer_basis: general_knowledge`, including restored
history. Course answers keep their citations and source list and have no
`answer_basis`.

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

## Understanding failures and the fallback (EC-022)

Every question first makes one model call to understand it. That call is retried on transient trouble only (rate limit, timeout,
connection error, HTTP 408/409/429/5xx) with backoff (1 s, then 2.5 s, plus jitter), honoring `Retry-After` up to 8 s, for at most
three attempts at 25 s each. Bad keys, exhausted quota and bad requests are never retried. Each retry, failure and recovery is saved
as an event (`assistant_events`: stage, outcome, error class, HTTP status, attempt; never question text, answers or keys) and
summarized by `GET /api/assistant/diagnostics?hours=24`.

If understanding still fails, or the model's plan is rejected, a **safe subset** of questions falls back to a plain course-material
search (`course_wide_question`) and the answer carries the notice "Question understanding was unavailable, so I searched your
course material directly. Live Canvas details such as deadlines were not checked." The fallback is **refused** (the student sees the
usual retry message) for: live Canvas facts (due dates, deadlines, submissions, points, "my grade"), specific numbered work
(homework 1, lab 3, quiz 2, chapter 5, ... so one can never become ten), solution requests, follow-ups ("and the written one?",
"that", "again"), and questions under four words. AI-use restrictions are decided from the question text itself, so they apply on
the fallback path unchanged.

## Date phrases

Deadline questions are resolved by `structured_canvas_service.date_window` in the **course's time zone** (not UTC), as a half-open
window, with no model involved. Supported: today, tonight (the rest of today), tomorrow, the day after tomorrow, yesterday, weekdays
(`friday` is the next one, today if it is that day; `next friday` is that weekday of the following calendar week), this/next/last
week, rest/end of the week, this/next weekend, this/next month, `in 3 days` / `in two weeks`, dates (`2026-11-20`, `11/20`,
`Nov 20th`, `20 November`, `the 20th`), and `by` / `before` / `until` / `through` / `no later than` any of those (**by Friday** means
everything from today through the end of Friday; **before Friday** stops at the start of Friday). Undated months and days mean the
nearest sensible year or month. `11/20` is read month/day. The qualifier the model drops ("by Friday" returned as "Friday") is restored from the student's own words. "What is due?" with no date means the next 14 days (`upcoming`, `soon`, `coming up`); a request for all or every assignment still lists everything. A deadline or list question that asks about several courses is told the assistant answers one course at a time. A request the model labels a plain assignment list but that names a date ("what is due tonight?") is answered as a deadline lookup for that window, with each item's submission status (EC-023). Anything unclear (including `by banana`, impossible dates, `in 0 days`,
text over 80 characters) is refused with a message listing what is supported, never guessed.

## Course logistics come from the syllabus

Questions about office hours, the instructor's name/email/contact, the grading scale or breakdown, class meeting time/location, or the syllabus
itself are answered from the syllabus only, the same way policy questions are, so announcements ("office hours canceled") cannot outrank the
actual hours. Regular hours therefore come from the syllabus, and a cancellation announcement is not mentioned unless asked about. Model-emitted
hidden citation markup and duplicate "(S3)" labels are removed from every answer; the backend's own `[S3]` labels are the only citations shown.

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

`assignment_policy_service.py` reads restrictions from backend-resolved native
assignment instructions, related non-solution instruction documents and, for
AI use, the course syllabus. This includes module-related/file-only work.
Bounded policy/submission excerpts are reserved ahead of competing solution
passages, with original source IDs and page/slide/block metadata where
available. A solution file does not erase the applicable question-document
policy. Instructor-template sources remain excluded.

AI/GenAI-generated-work bans, limited AI permissions, outside-assistance bans
and individual-work requirements are separate rule categories.
Submission-format requirements are preserved separately; they do not
automatically become an AI ban.

### AI-use statements (EC-015)

`ai_use_policy.py` classifies each sentence that names an AI tool in a usage
sense. No model is called. Lines wrapped by PDF extraction are rejoined first.

- **Prohibited:** direct bans ("do not use", "not allowed to use", "is banned",
  "off limits", "this course prohibits the use of"), "must be completed without
  AI", and penalty or misconduct framing ("will be treated as cheating", "will
  receive a zero").
- **Limited:** permission restricted to stated purposes ("for brainstorming
  only", "to check grammar") or to instructor authorization. A limited
  permission does not cover producing the assigned work.
- **Permitted:** explicit permission ("you may use", "this course permits the
  use of"). Permissive wording is never treated as a prohibition.
- **No statement:** AI named as a course topic ("the AI lab", "no AI background
  is required"), a rule that only penalizes uncited use, or a condition ("when
  AI use is authorized, cite it").

Each statement records whether the rule itself defers to authorization
("unless permitted by the instructor", "unauthorized use", "without written
permission") and whether it is limited to a kind of work by an explicit scoping
phrase ("on exams", "for homework"). Unscoped statements cover all course work.

### Scope and resolution

- **Assignment-specific:** statements in an assignment's description or linked
  instruction documents apply only to that assignment.
- **Course-wide:** statements in the syllabus apply to every resolved
  assignment, and to a named work file without a Canvas assignment record (for
  example a project specification). They do not apply to lecture or practice
  material, or to concept questions. A statement scoped to a work type applies
  only to assignments of that type, judged from the title and Canvas quiz type.
- **Same source:** a rule that defers to authorization is satisfied by an
  explicit permission in the same source. A syllabus that says unauthorized AI
  use is a breach and then permits AI for assignments is not a ban. A flat
  prohibition is never lifted by a permission elsewhere in the same source.
- **Authorized exception:** if every applicable syllabus rule defers to
  authorization and the assignment explicitly permits AI, the assignment is not
  restricted. The exception is recorded in `assignment_policy.exceptions`.
- **Conflict:** if the syllabus prohibits AI flatly and the assignment permits
  it, permission is not assumed. The syllabus rule is enforced, the response
  explains the conflict and cites both sources, and every answer about that
  assignment carries a notice. If the syllabus permits AI and the assignment
  prohibits it, the assignment's restriction applies and the response says so.
  Conflicts are recorded in `assignment_policy.conflicts`.

These are deterministic English-language rules, not a complete interpreter for
arbitrary institutional policies. Unrecognized wording yields no statement, so
it restricts nothing.

### Requests that do not name the assignment

A restriction follows the work the answer would come from, not the wording of
the request.

- **Solution request:** recognized from the model's `solve_question` intent,
  from solution wording, or from a plain request in the student's words ("write
  the code for HW 3", "do my homework"). Enforcement does not depend only on
  the model's classification.
- **Follow-ups:** a turn that continues the conversation keeps its resolved
  assignment, so a solution request after an explanation, or a retry after a
  refusal, is checked against the same rules.
- **Pasted or reworded question:** when a solution request names no assignment,
  its three best-matching retrieved passages decide. If assigned work is among
  them (a Canvas assignment, a document linked to one, or a work file), that
  work's own rules and the course-wide rules apply. Real similarity scores are
  close together, and a lecture slide can outrank the assignment it quotes, so
  one passage is not enough. A passage scoring below three quarters of the best
  match is ignored, and a result with no applicable restriction is returned
  unchanged. The response says the question closely matches assigned work.
- **Solutions file:** a request for a solutions or answer-key document under a
  restriction is a solution request however it is worded. Such a file counts as
  assigned work even when it is linked to no assignment and its title names no
  kind of work.

Not covered: a question about assigned work that uses no solution wording and
names no assignment is answered as a course question, and the general-knowledge
route retrieves nothing, so it cannot be matched to an assignment.

Known trade-off: in a restricted course, a solution request for a problem the
student made up is refused when its best-matching passages are assigned work on
the same topic. The assistant still explains the concept when asked without
solution wording.

### Enforcement

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
- During a course sync, and after a failed or interrupted one, document, policy
  and saved-rubric routes keep answering from the last published material (EC-012;
  see the ingestion spec's "Sync generations"). This applies from the start of a
  course's sync, including Canvas discovery. Answers carry a `material_notice`,
  such as "Canvas sync is in progress. This answer uses course material saved by
  the last completed sync." The Study Assistant shows it as a subtle line under the
  answer, outside the collapsed "Material limitations" list (where it is not
  repeated). Cited sources that are a retained previous
  version, or a cache that could not be refreshed, add their own warning.
  Live current-fact routes (deadlines, points, submission status, lists) still
  fetch Canvas and never carry the saved-material notice. Retrieval uses the
  served view (`served=True`).
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
