from contextlib import asynccontextmanager
from uuid import UUID
from fastapi.responses import StreamingResponse
from backend.app.conversation_service import ConversationService, ConversationError
from backend.app.canvas_service import get_cache_namespace
import sqlite3
from typing import Literal
import requests

from fastapi import FastAPI, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field, StrictInt, field_validator
from fastapi.middleware.cors import CORSMiddleware

from backend.app.canvas_service import (
    get_current_courses,
    get_clean_assignments,
    get_upcoming_assignments,
)
from backend.app.course_sync_service import CourseSyncService
from backend.app import priority_service
from backend.app import visual_document_service
from backend.app import openai_key_service
from backend.app import consent_service
from backend.app.consent_service import AIConsentRequired
from fastapi.responses import JSONResponse
from backend.app import schedule_service
from backend.app import study_plan_service
from backend.app import course_overview_service
from backend.app import assistant_events
from starlette.concurrency import run_in_threadpool
from backend.app import rag_service
from backend.app import retention_service
from backend.app import search_index_service
from backend.app.api_usage_service import usage_summary
from backend.app.embedding_service import EmbeddingProviderError, default_store
from backend.app.conversation_store import ConversationStore, validate_title
from backend.app.llm_service import LLMProviderError


@asynccontextmanager
async def lifespan(app):
    try:
        service = CourseSyncService()
        service.start()
        app.state.course_sync = service
        app.state.course_sync_error = None
    except (ValueError, OSError, sqlite3.Error):
        # Configuration/storage failures must not prevent health/dashboard routes.
        app.state.course_sync = None
        app.state.course_sync_error = "Course sync could not start. Check backend configuration and storage."
    try:
        yield
    finally:
        if app.state.course_sync is not None:
            app.state.course_sync.stop()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status":"ok"}

@app.get("/api/courses")
def courses():
    return get_current_courses()


@app.get("/api/courses/{course_id}/assignments")
def assignments(course_id: int):
    return get_clean_assignments(course_id)


@app.get("/api/courses/{course_id}/upcoming")
def upcoming(course_id: int):
    return get_upcoming_assignments(course_id)


@app.get("/api/priorities")
def priorities(response: Response, refresh: bool = False):
    """Read-only Canvas facts for ranking; refresh=true bypasses the 60-second cache."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return priority_service.get_priorities(refresh=refresh)
    except (requests.RequestException, ValueError, KeyError):
        raise HTTPException(status_code=502, detail="Unable to load assignments from Canvas.")


@app.get("/api/ingestion")
def ingestion_status(request: Request):
    service = request.app.state.course_sync
    if service is None:
        return {"status": "error", "message": request.app.state.course_sync_error, "courses": []}
    return service.status()


@app.post("/api/ingestion/sync", status_code=202)
def start_ingestion(request: Request):
    service = request.app.state.course_sync
    if service is None:
        raise HTTPException(status_code=503, detail=request.app.state.course_sync_error)
    try:
        return service.request_sync()
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail="Course sync could not start. Check the backend and retry.") from None


@app.get('/api/storage')
def storage_preview():
    try:
        return retention_service.preview_cleanup()
    except (ValueError, OSError, sqlite3.Error, requests.RequestException):
        raise HTTPException(status_code=503, detail='Unable to confirm course storage. Check Canvas access and retry.') from None


class CourseRetentionChoice(BaseModel):
    keep: bool = Field(strict=True)


@app.patch('/api/storage/courses/{course_id}')
def keep_course_materials(body: CourseRetentionChoice, course_id: int = Path(gt=0)):
    try:
        default_store().set_course_kept(course_id, body.keep)
        return {'course_id': course_id, 'kept': body.keep}
    except ValueError:
        raise HTTPException(status_code=404, detail='Saved course not found.') from None
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail='Unable to save your retention choice. Please retry.') from None


class StorageCleanup(BaseModel):
    course_ids: list[StrictInt] = Field(min_length=1, max_length=100)
    preview_id: str = Field(pattern=r'^[a-f0-9]{64}$')


@app.post('/api/storage/cleanup')
def cleanup_storage(body: StorageCleanup):
    try:
        return retention_service.delete_materials(body.course_ids, body.preview_id)
    except retention_service.RetentionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError:
        raise HTTPException(status_code=422, detail='Select valid saved course IDs.') from None
    except (OSError, sqlite3.Error, requests.RequestException):
        raise HTTPException(status_code=503, detail='Unable to complete cleanup. Refresh the preview and retry.') from None


class SearchSettings(BaseModel):
    model_config = {'extra': 'forbid'}
    auto_index: bool | None = Field(default=None, strict=True)
    vision_enabled: bool | None = Field(default=None, strict=True)
    monthly_warning_usd: float | None = Field(default=None, strict=True, ge=0, le=100000, allow_inf_nan=False)


def _settings_view(store, settings=None):
    """Saved preferences plus the vision setting actually in effect (Settings choice, else .env default)."""
    settings = dict(settings if settings is not None else store.load_settings())
    saved = settings.get('vision_enabled')
    settings['vision_from_environment'] = not isinstance(saved, bool)
    settings['vision_enabled'] = visual_document_service.vision_enabled(store)
    return settings


@app.get('/api/search/settings')
def search_settings():
    try:
        store = default_store()
        return {'settings': _settings_view(store), 'usage': usage_summary(store), 'api_key': openai_key_service.key_status(store)}
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Search settings are unavailable. Please retry.') from None


@app.patch('/api/search/settings')
def change_search_settings(body: SearchSettings, request: Request):
    changes = body.model_dump(exclude_unset=True)
    if changes.get('auto_index', True) is None:
        raise HTTPException(status_code=422, detail='Choose whether automatic updates are enabled.')
    if changes.get('vision_enabled', True) is None:
        raise HTTPException(status_code=422, detail='Choose whether image and scanned-page reading is enabled.')
    try:
        store = default_store()
        settings = store.update_settings(changes)
        if changes.get('auto_index'):
            for course_id in store.saved_current_course_ids():
                snapshot = store.load_course(course_id)
                if snapshot and not snapshot.get('materials_removed'):
                    search_index_service.queue_course(course_id, store=store)
            service = getattr(request.app.state, 'course_sync', None)
            if service:
                service.ensure_worker()
        return {'settings': _settings_view(store, settings), 'usage': usage_summary(store), 'api_key': openai_key_service.key_status(store)}
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Search settings could not be saved. Please retry.') from None


class MeetingFields(BaseModel):
    model_config = {'extra': 'forbid'}
    days: list[str] | None = None
    start_time: str | None = Field(default=None, max_length=5)
    end_time: str | None = Field(default=None, max_length=5)
    kind: str | None = Field(default=None, max_length=20)
    location: str | None = Field(default=None, max_length=schedule_service.MAX_LOCATION * 2)


class NewMeeting(MeetingFields):
    course_id: StrictInt
    source: Literal['manual', 'syllabus'] = 'manual'
    source_text: str | None = Field(default=None, max_length=schedule_service.MAX_QUOTE * 2)
    fingerprint: str | None = Field(default=None, pattern=r'^[0-9a-f]{16}$')


class DismissedSuggestion(BaseModel):
    model_config = {'extra': 'forbid'}
    course_id: StrictInt
    fingerprint: str = Field(pattern=r'^[0-9a-f]{16}$')


def _schedule_call(action):
    """Map schedule errors to safe HTTP responses."""
    try:
        return action(default_store())
    except schedule_service.ScheduleError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='The class schedule is unavailable. Please retry.') from None


def _known_course(store, course_id):
    if course_id not in store.saved_current_course_ids():
        raise LookupError('Course not found. Sync Canvas first.')


@app.get('/api/schedule')
def class_schedule():
    """Confirmed meetings plus syllabus suggestions per saved course. Reads saved data only."""
    return _schedule_call(schedule_service.build_schedule)


@app.post('/api/schedule/meetings', status_code=201)
def add_class_meeting(body: NewMeeting):
    def create(store):
        _known_course(store, body.course_id)
        data = body.model_dump(exclude={'course_id', 'source', 'source_text', 'fingerprint'})
        return schedule_service.create_meeting(store, body.course_id, data, source=body.source,
                                               source_text=body.source_text, fingerprint_value=body.fingerprint)
    return _schedule_call(create)


@app.put('/api/schedule/meetings/{meeting_id}')
def edit_class_meeting(body: MeetingFields, meeting_id: str = Path(pattern=r'^[0-9a-f]{32}$')):
    return _schedule_call(lambda store: schedule_service.update_meeting(
        store, meeting_id, body.model_dump(exclude_unset=True)))


@app.delete('/api/schedule/meetings/{meeting_id}')
def delete_class_meeting(meeting_id: str = Path(pattern=r'^[0-9a-f]{32}$')):
    def delete(store):
        if not store.delete_class_meeting(meeting_id):
            raise LookupError('Meeting not found.')
    _schedule_call(delete)
    return {'ok': True}


@app.post('/api/schedule/suggestions/dismiss')
def dismiss_schedule_suggestion(body: DismissedSuggestion):
    def dismiss(store):
        _known_course(store, body.course_id)
        schedule_service.dismiss_suggestion(store, body.course_id, body.fingerprint)
    _schedule_call(dismiss)
    return {'ok': True}


@app.delete('/api/schedule/courses/{course_id}/dismissed')
def restore_schedule_suggestions(course_id: int = Path(gt=0)):
    def restore(store):
        _known_course(store, course_id)
        store.delete_dismissed_suggestions(course_id)
    _schedule_call(restore)
    return {'ok': True}


# ---- study plans (Stage A: the student's own steps and a rules-based search for free time; no AI) ----
PLAN_ID = r'^[0-9a-f]{32}$'


class NewPlan(BaseModel):
    model_config = {'extra': 'forbid'}
    course_id: StrictInt
    assignment_id: StrictInt | None = None
    title: str | None = Field(default=None, max_length=300)
    due_at: str | None = Field(default=None, max_length=40)


class PlanPreferences(BaseModel):
    model_config = {'extra': 'forbid'}
    day_start: str | None = Field(default=None, max_length=5)
    day_end: str | None = Field(default=None, max_length=5)
    max_minutes_per_day: StrictInt | None = None
    buffer_minutes: StrictInt | None = None


class NewStep(BaseModel):
    model_config = {'extra': 'forbid'}
    title: str = Field(max_length=300)
    minutes: StrictInt


class StepChanges(BaseModel):
    model_config = {'extra': 'forbid'}
    title: str | None = Field(default=None, max_length=300)
    minutes: StrictInt | None = None
    done: bool | None = Field(default=None, strict=True)


class PlannedSession(BaseModel):
    model_config = {'extra': 'forbid'}
    activity_id: str = Field(pattern=PLAN_ID)
    start_at: str = Field(max_length=40)
    end_at: str = Field(max_length=40)


class ChosenTime(BaseModel):
    model_config = {'extra': 'forbid'}
    start_at: str = Field(max_length=40)
    replace_conflicts: bool = Field(default=False, strict=True)


class PlannedSessions(BaseModel):
    model_config = {'extra': 'forbid'}
    sessions: list[PlannedSession] = Field(max_length=study_plan_service.MAX_ACTIVITIES)


def _plan_call(action):
    """Map plan errors to safe HTTP responses."""
    try:
        return action(default_store())
    except study_plan_service.PlanConflict as conflict:
        # 409: the time is booked. The body lists what is there so the student can choose to add it anyway.
        return JSONResponse(status_code=409, content={'detail': str(conflict), 'conflicts': conflict.conflicts})
    except study_plan_service.PlanError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except requests.RequestException:
        raise HTTPException(status_code=503, detail='Canvas could not be reached, so the assignment could not be checked. Please retry.') from None
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Study plans are unavailable. Please retry.') from None


def _canvas_assignment(course_id, assignment_id):
    """The assignment as Canvas reports it now (same data and cache as Priorities); None when it is not there."""
    for item in priority_service.get_priorities().get('assignments', []):
        if item.get('course_id') == course_id and item.get('id') == assignment_id:
            return item
    return None


@app.get('/api/study-plans')
def study_plans():
    return _plan_call(study_plan_service.build_plans)


@app.put('/api/study-plans/preferences')
def study_plan_preferences(body: PlanPreferences):
    return _plan_call(lambda store: study_plan_service.set_preferences(store, body.model_dump(exclude_none=True)))


@app.post('/api/study-plans', status_code=201)
def create_study_plan(body: NewPlan):
    return _plan_call(lambda store: study_plan_service.create_plan(store, body.model_dump(), fetch_assignment=_canvas_assignment))


@app.delete('/api/study-plans/{plan_id}')
def delete_study_plan(plan_id: str = Path(pattern=PLAN_ID)):
    _plan_call(lambda store: study_plan_service.delete_plan(store, plan_id))
    return {'ok': True}


@app.post('/api/study-plans/{plan_id}/activities', status_code=201)
def add_plan_step(body: NewStep, plan_id: str = Path(pattern=PLAN_ID)):
    return _plan_call(lambda store: study_plan_service.add_activity(store, plan_id, body.model_dump()))


@app.patch('/api/study-plans/{plan_id}/activities/{activity_id}')
def edit_plan_step(body: StepChanges, plan_id: str = Path(pattern=PLAN_ID), activity_id: str = Path(pattern=PLAN_ID)):
    return _plan_call(lambda store: study_plan_service.update_activity(store, plan_id, activity_id, body.model_dump(exclude_unset=True)))


@app.delete('/api/study-plans/{plan_id}/activities/{activity_id}')
def delete_plan_step(plan_id: str = Path(pattern=PLAN_ID), activity_id: str = Path(pattern=PLAN_ID)):
    _plan_call(lambda store: study_plan_service.delete_activity(store, plan_id, activity_id))
    return {'ok': True}


@app.post('/api/study-plans/{plan_id}/suggest')
def suggest_plan_times(plan_id: str = Path(pattern=PLAN_ID)):
    """Propose times for the unscheduled steps. Nothing is saved until the student accepts."""
    return _plan_call(lambda store: study_plan_service.suggest_times(store, plan_id))


@app.put('/api/study-plans/{plan_id}/times')
def accept_plan_times(body: PlannedSessions, plan_id: str = Path(pattern=PLAN_ID)):
    return _plan_call(lambda store: study_plan_service.accept_times(store, plan_id, [s.model_dump() for s in body.sessions]))


@app.put('/api/study-plans/{plan_id}/activities/{activity_id}/time')
def choose_plan_step_time(body: ChosenTime, plan_id: str = Path(pattern=PLAN_ID), activity_id: str = Path(pattern=PLAN_ID)):
    """The student picks when to study a step. 409 with the overlaps unless replace_conflicts is true."""
    return _plan_call(lambda store: study_plan_service.set_time(store, plan_id, activity_id, body.start_at, replace=body.replace_conflicts))


@app.delete('/api/study-plans/{plan_id}/activities/{activity_id}/time')
def clear_plan_step_time(plan_id: str = Path(pattern=PLAN_ID), activity_id: str = Path(pattern=PLAN_ID)):
    _plan_call(lambda store: study_plan_service.clear_time(store, plan_id, activity_id))
    return {'ok': True}


@app.get('/api/course-catalog')
def course_catalog():
    """Per-course summaries for the Courses page. Saved data only."""
    return _schedule_call(course_overview_service.build_catalog)


@app.get('/api/courses/{course_id}/details')
def course_details(course_id: int = Path(gt=0)):
    """Saved announcements, modules, counts and Canvas links for one course page."""
    return _schedule_call(lambda store: course_overview_service.build_details(store, course_id))


@app.get('/api/assistant/diagnostics')
def assistant_diagnostics(hours: int = Query(default=24, ge=1, le=720)):
    """Retries, failures and fallbacks of the assistant's model calls (error classes only; never questions or answers)."""
    try:
        return assistant_events.summarize(default_store(), hours=hours)
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Assistant diagnostics are unavailable. Please retry.') from None


def _consent_denied():
    """403 with a stable code, so clients can tell missing consent from retrieval failures and model errors."""
    return JSONResponse(status_code=403, content={'detail': consent_service.MESSAGE, 'code': consent_service.ERROR_CODE})


def _require_consent_or_403():
    """Route-level gate for AI endpoints; services and providers enforce it again at the actual boundary."""
    try:
        store = default_store()
    except (OSError, sqlite3.Error, ValueError):
        store = None  # unknown consent is denied
    consent_service.require_ai_consent(store)  # AIConsentRequired -> 403 via the handler below


@app.exception_handler(AIConsentRequired)
async def _consent_required_handler(request, exc):
    return _consent_denied()


class ConsentDecision(BaseModel):
    model_config = {'extra': 'forbid'}
    decision: Literal['granted', 'declined', 'withdrawn']
    version: StrictInt


@app.get('/api/ai-consent')
def ai_consent_status():
    try:
        return consent_service.consent_view(default_store())
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='AI consent status is unavailable. Please retry.') from None


@app.put('/api/ai-consent')
def ai_consent_decide(body: ConsentDecision, request: Request):
    try:
        store = default_store()
        try:
            view = consent_service.record_decision(store, body.decision, body.version)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        if view['enabled']:
            # Resume safely: queue_course only queues chunks that still lack embeddings, and only if automatic updates are on.
            for course_id in store.saved_current_course_ids():
                snapshot = store.load_course(course_id)
                if snapshot and not snapshot.get('materials_removed'):
                    search_index_service.queue_course(course_id, store=store)
            service = getattr(request.app.state, 'course_sync', None)
            if service:
                service.ensure_worker()
        return view
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Your AI choice could not be saved. Please retry.') from None


@app.get('/api/openai-key')
def openai_key_status():
    """Whether a key is in use and its last 4 characters. The key itself is never returned."""
    try:
        return openai_key_service.key_status(default_store())
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='The API key status is unavailable. Please retry.') from None


@app.put('/api/openai-key')
async def save_openai_key(request: Request):
    _require_consent_or_403()
    """Verify the key with OpenAI (a free request), then save it locally. Errors never echo the key."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail='Send the key as JSON: {"api_key": "..."}.') from None
    if not isinstance(body, dict) or set(body) != {'api_key'}:
        raise HTTPException(status_code=422, detail='Send the key as JSON: {"api_key": "..."}.')
    try:
        return await run_in_threadpool(openai_key_service.save_key, default_store(), body['api_key'])
    except openai_key_service.KeyRejected as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except openai_key_service.KeyUnverifiable as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='The API key could not be saved. Please retry.') from None


@app.delete('/api/openai-key')
def remove_openai_key():
    """Forget the saved key; EasyCanvas falls back to OPENAI_API_KEY in .env if one is set."""
    try:
        return openai_key_service.remove_key(default_store())
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='The saved API key could not be removed. Please retry.') from None


@app.get('/api/search/status')
def all_search_status():
    try:
        store = default_store()
        return {'courses': [search_index_service.index_status(course_id, store=store)
                            for course_id in store.saved_current_course_ids()
                            if not store.load_course(course_id).get('materials_removed')]}
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Course search status is unavailable. Please retry.') from None


@app.get('/api/courses/{course_id}/search-index')
def course_search_status(course_id: int = Path(gt=0)):
    try:
        return search_index_service.index_status(course_id)
    except ValueError:
        raise HTTPException(status_code=404, detail='Saved course materials not found. Sync Canvas first.') from None
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail='Course search status is unavailable. Please retry.') from None


@app.post('/api/courses/{course_id}/search-index', status_code=202)
def update_course_search(request: Request, course_id: int = Path(gt=0)):
    _require_consent_or_403()
    try:
        status = search_index_service.queue_course(course_id, manual=True)
        service = getattr(request.app.state, 'course_sync', None)
        if service is None:
            raise HTTPException(status_code=503, detail='Course search worker is unavailable. Restart the backend and retry.')
        service.ensure_worker()
        return status
    except ValueError:
        raise HTTPException(status_code=404, detail='Saved course materials not found. Sync Canvas first.') from None
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail='Course search could not be updated. Please retry.') from None


class AssistantQuestion(BaseModel):
    """Validate a question before any paid provider request is made."""

    question: str = Field(strict=True, min_length=1, max_length=8000)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value):
        if not value.strip() or len(value.encode("utf-8")) > 8000:
            raise ValueError("Question must be nonblank and at most 8,000 UTF-8 bytes.")
        return value


@app.post("/api/courses/{course_id}/assistant")
def assistant(body: AssistantQuestion, course_id: int = Path(gt=0)):
    """Expose the existing stateless RAG service without duplicating its logic."""
    _require_consent_or_403()
    try:
        return rag_service.answer_question(course_id, body.question)
    except AIConsentRequired:
        return _consent_denied()
    except rag_service.RAGValidationError:
        raise HTTPException(status_code=502, detail="The assistant returned an invalid answer. Please retry.") from None
    except (LLMProviderError, EmbeddingProviderError):
        raise HTTPException(status_code=503, detail="The assistant provider is unavailable. Please retry later.") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="The course or assistant configuration is unavailable. Check the selected course and backend setup.") from None
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail="Saved course data is unavailable. Please retry later.") from None
    except Exception:
        # Never expose unexpected provider/storage details through the API.
        raise HTTPException(status_code=500, detail="The assistant could not complete this request.") from None



conversations = ConversationService(repository=ConversationStore())


def _conversation_account(course_id):
    """Validate the saved selected course before accessing private chat history."""
    try:
        store = default_store()
        if store.load_course(course_id) is None:
            raise HTTPException(status_code=404, detail='Saved course not found.')
        return store.namespace
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Saved conversation data is unavailable.') from None


@app.get('/api/courses/{course_id}/conversations')
def conversation_list(course_id: int = Path(gt=0)):
    try:
        return conversations.repository.list(_conversation_account(course_id), course_id)
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail='Conversation history is unavailable. Please retry.') from None


@app.post('/api/courses/{course_id}/conversations', status_code=201)
def conversation_create(course_id: int = Path(gt=0)):
    try:
        namespace = _conversation_account(course_id)
        session = conversations.acquire(namespace, course_id)
        session.busy = False
        return conversations.repository.load(namespace, course_id, session.id)
    except (OSError, sqlite3.Error, ConversationError):
        raise HTTPException(status_code=503, detail='Unable to create a conversation. Please retry.') from None


@app.get('/api/courses/{course_id}/conversations/{conversation_id}')
def conversation_detail(conversation_id: UUID, course_id: int = Path(gt=0)):
    try:
        result = conversations.repository.load(_conversation_account(course_id), course_id, str(conversation_id), touch=True)
        if result is None:
            raise HTTPException(status_code=404, detail='Conversation not found for this course.')
        return result
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail='Unable to load this conversation. Please retry.') from None


class ConversationRename(BaseModel):
    title: str = Field(strict=True, min_length=1, max_length=100)

    @field_validator('title', mode='before')
    @classmethod
    def clean_title(cls, value):
        return validate_title(value)


@app.patch('/api/courses/{course_id}/conversations/{conversation_id}')
def conversation_rename(body: ConversationRename, conversation_id: UUID, course_id: int = Path(gt=0)):
    try:
        result = conversations.repository.rename(_conversation_account(course_id),course_id,str(conversation_id),body.title)
        if result is None:
            raise HTTPException(status_code=404,detail='Conversation not found for this course.')
        return result
    except (OSError,sqlite3.Error):
        raise HTTPException(status_code=503,detail='Unable to rename this conversation. Please retry.') from None


@app.delete('/api/courses/{course_id}/conversations/{conversation_id}')
def conversation_delete(conversation_id: UUID, course_id: int = Path(gt=0)):
    try:
        if not conversations.delete(_conversation_account(course_id),course_id,str(conversation_id)):
            raise HTTPException(status_code=404,detail='Conversation not found for this course.')
        return {'conversation_id':str(conversation_id),'course_id':course_id,'deleted':True}
    except ConversationError as error:
        raise HTTPException(status_code=409,detail=str(error)) from None
    except (OSError,sqlite3.Error):
        raise HTTPException(status_code=503,detail='Unable to delete this conversation. Please retry.') from None


class ConversationQuestion(AssistantQuestion):
    conversation_id: UUID | None = None
    turn_id: UUID


@app.post("/api/courses/{course_id}/assistant/stream")
async def assistant_stream(body: ConversationQuestion, request: Request, course_id: int = Path(gt=0)):
    """A thin SSE adapter; session, retrieval and generation stay in services."""
    import asyncio
    import json
    _require_consent_or_403()
    try:
        session = conversations.acquire(get_cache_namespace(), course_id,
                                        str(body.conversation_id) if body.conversation_id else None)
    except ConversationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError:
        raise HTTPException(status_code=503, detail="Assistant configuration is unavailable.") from None

    async def stream():
        iterator = conversations.events(session, body.question, str(body.turn_id))
        task = None
        try:
            while True:
                task = asyncio.create_task(anext(iterator))
                while not task.done():
                    done, _ = await asyncio.wait({task}, timeout=10)
                    if await request.is_disconnected():
                        return
                    if not done:
                        yield ": keepalive\n\n"
                try:
                    event = task.result()
                except StopAsyncIteration:
                    return
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        finally:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
            await iterator.aclose()
            session.busy = False
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
