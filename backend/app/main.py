from contextlib import asynccontextmanager
import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.app.canvas_service import (
    get_current_courses,
    get_clean_assignments,
    get_upcoming_assignments,
)
from backend.app.course_sync_service import CourseSyncService


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
