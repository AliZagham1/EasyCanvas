from contextlib import asynccontextmanager
from uuid import UUID
from fastapi.responses import StreamingResponse
from backend.app.conversation_service import ConversationService, ConversationError
from backend.app.canvas_service import get_cache_namespace
import sqlite3
import requests

from fastapi import FastAPI, HTTPException, Path, Request
from pydantic import BaseModel, Field, StrictInt, field_validator
from fastapi.middleware.cors import CORSMiddleware

from backend.app.canvas_service import (
    get_current_courses,
    get_clean_assignments,
    get_upcoming_assignments,
)
from backend.app.course_sync_service import CourseSyncService
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
    monthly_warning_usd: float | None = Field(default=None, strict=True, ge=0, le=100000, allow_inf_nan=False)


@app.get('/api/search/settings')
def search_settings():
    try:
        store = default_store()
        return {'settings': store.load_settings(), 'usage': usage_summary(store)}
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Search settings are unavailable. Please retry.') from None


@app.patch('/api/search/settings')
def change_search_settings(body: SearchSettings, request: Request):
    changes = body.model_dump(exclude_unset=True)
    if changes.get('auto_index', True) is None:
        raise HTTPException(status_code=422, detail='Choose whether automatic updates are enabled.')
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
        return {'settings': settings, 'usage': usage_summary(store)}
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail='Search settings could not be saved. Please retry.') from None


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
    try:
        return rag_service.answer_question(course_id, body.question)
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
