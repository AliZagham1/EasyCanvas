"""Bounded model context, with optional account/course-isolated chat persistence."""

import asyncio
from contextlib import aclosing
import copy
import time
import uuid
import threading
from dataclasses import dataclass, field

from backend.app import rag_service
from backend.app.embedding_service import default_store, validate_course_id, EmbeddingProviderError
from backend.app.llm_service import OpenAIAnswerProvider, LLMProviderError
from backend.app.query_understanding_service import understand_question
from backend.app.clarification_service import reply_selection, reply_plan
from backend.app.conversation_state_service import next_state, answer_context


class ConversationError(ValueError):
    """A safe session validation error, not an upstream failure."""


@dataclass
class Conversation:
    namespace: str
    course_id: int
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    history: list = field(default_factory=list)
    active_documents: list = field(default_factory=list)
    active_assignments: list = field(default_factory=list)
    clarification: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    turns: dict = field(default_factory=dict)
    busy: bool = False
    updated_at: float = field(default_factory=time.monotonic)


class ConversationService:
    """Single-worker memory with TTL, bounded history and retry idempotency."""

    def __init__(self, ttl=1800, limit=100, repository=None):
        self.sessions = {}
        self.ttl, self.limit = ttl, limit
        self.repository = repository
        self.lock = threading.RLock()

    def acquire(self, namespace, course_id, conversation_id=None):
        with self.lock:
            return self._acquire(namespace, course_id, conversation_id)

    def _acquire(self, namespace, course_id, conversation_id=None):
        validate_course_id(course_id)
        now = time.monotonic()
        for key in list(self.sessions):
            session = self.sessions[key]
            if not session.busy and now - session.updated_at > self.ttl:
                del self.sessions[key]
        if conversation_id:
            session = self.sessions.get(conversation_id)
            if session is None and self.repository:
                saved = self.repository.load(namespace, course_id, conversation_id)
                if saved:
                    state = saved['state']
                    session = Conversation(namespace, course_id, id=conversation_id,
                        active_documents=state.get('active_documents', []), active_assignments=state.get('active_assignments', []),
                        clarification=state.get('clarification', {}), state=state.get('semantic', {}))
                    users = {m['turn_id']:m['content'] for m in saved['messages'] if m['role'] == 'user'}
                    replies = [m for m in saved['messages'] if m['role'] == 'assistant']
                    session.history = [{'question':users[m['turn_id']], 'answer':m['content']} for m in replies[-3:]]
                    session.turns = {m['turn_id']:(users[m['turn_id']],m['response']) for m in replies[-12:]}
                    self.sessions[session.id] = session
            if session is None or session.namespace != namespace or session.course_id != course_id:
                raise ConversationError("This conversation is unavailable for the selected course. Start a new conversation.")
        else:
            if len(self.sessions) >= self.limit:
                idle = [s for s in self.sessions.values() if not s.busy]
                if not idle:
                    raise ConversationError("The assistant is busy. Please retry shortly.")
                del self.sessions[min(idle, key=lambda s: s.updated_at).id]
            session = Conversation(namespace, course_id)
            if self.repository:
                self.repository.create(namespace, course_id, session.id)
            self.sessions[session.id] = session
        if session.busy:
            raise ConversationError("A response is already in progress for this conversation.")
        session.busy = True
        session.updated_at = now
        return session

    def delete(self, namespace, course_id, conversation_id):
        """Serialize deletion with session acquisition so an active turn is safe."""
        with self.lock:
            session = self.sessions.get(conversation_id)
            if session and (session.namespace != namespace or session.course_id != course_id):
                return False
            if session and session.busy:
                raise ConversationError('Wait for the response to finish or stop it before deleting this conversation.')
            deleted = self.repository.delete(namespace, course_id, conversation_id)
            if deleted:
                self.sessions.pop(conversation_id, None)
            return deleted

    @staticmethod
    def context(session, store):
        documents = {doc["document_key"]: doc for doc in store.load_documents(session.course_id, readable_only=True)}
        active = [{"document_key": key, "title": documents[key].get("title")} for key in session.active_documents if key in documents]
        # At most three completed turns and 6,000 characters of message content.
        remaining, history = 6000, []
        for turn in reversed(session.history[-3:]):
            entry = {}
            for role in ("question", "answer"):
                entry[role] = turn[role][:min(2000, remaining)]
                remaining -= len(entry[role])
            history.insert(0, entry)
        snapshot = store.load_course(session.course_id) or {}
        course = snapshot.get("course", {}).get("metadata", {})
        assignments = snapshot.get("assignments", [])
        active_assignments = [{"assignment_id": a["canvas_id"], "title": a.get("title")} for a in assignments
                              if a["canvas_id"] in session.active_assignments]
        return {"course_code": course.get("course_code"), "active_documents": active,
                "active_assignments": active_assignments, "recent_turns": history,
                "conversation_state": copy.deepcopy(session.state), "pending_clarification": copy.deepcopy(session.clarification)}

    async def events(self, session, question, turn_id, *, store=None, provider=None, embedding_provider=None):
        """Commit only completed validated turns; cancellation closes the provider."""
        fields = ('history', 'active_documents', 'active_assignments', 'clarification', 'state', 'turns')
        previous = {key: copy.deepcopy(getattr(session, key)) for key in fields}
        committed = False
        try:
            yield {"event": "session", "data": {"conversation_id": session.id, "turn_id": turn_id, "course_id": session.course_id}}
            if turn_id in session.turns:
                saved_question, result = session.turns[turn_id]
                if saved_question != question:
                    raise ConversationError("This turn ID was already used for another question.")
                yield {"event": "final", "data": copy.deepcopy(result)}
                return
            yield {"event": "searching", "data": {}}
            store = store or default_store()
            if store.namespace != session.namespace:
                raise ConversationError("Conversation account does not match the selected data.")
            if self.repository:
                await asyncio.to_thread(self.repository.begin_turn, session, turn_id, question)
            context = await asyncio.to_thread(self.context, session, store)
            from backend.app.api_usage_service import UsageRecorder
            provider = provider or OpenAIAnswerProvider(usage_recorder=UsageRecorder(store))
            selection = reply_selection(question, session.clarification, store.load_course(session.course_id) or {}, session.course_id)
            if selection is not None:
                understanding = reply_plan(question, selection)
                if not selection.get('clarification'):
                    context['clarification_selection'] = selection
            else:
                session.clarification = {}
                understanding = await understand_question(session.course_id, question, store=store,
                                                           provider=provider, conversation_context=context)
            query = understanding["query"]
            focus = [doc["document_key"] for doc in context["active_documents"]] if understanding["use_active_documents"] else []
            if not understanding["use_active_documents"]:
                context["active_documents"], context["active_assignments"] = [], []
                session.active_documents, session.active_assignments = [], []
            context = answer_context(context, understanding)
            async with aclosing(rag_service.stream_answer_question(
                    session.course_id, question, store=store, query=query, focus_documents=focus,
                    conversation_context=context, embedding_provider=embedding_provider, llm_provider=provider,
                    understanding=understanding)) as generation:
                async for event in generation:
                    if event["event"] == "final":
                        result = event["data"]
                        session.state = next_state(session.state, question, result, store.load_course(session.course_id) or {})
                        session.clarification = (result.get('clarification_candidates') or {}) if result['status'] == 'clarification_needed' else {}
                        session.turns[turn_id] = (question, copy.deepcopy(result))
                        while len(session.turns) > 12:
                            session.turns.pop(next(iter(session.turns)))
                        if result["status"] == "answered":
                            session.active_assignments = result.get("resolved_assignments", [])[:3]
                            session.history.append({"question": question, "answer": result["answer"]})
                            session.history = session.history[-3:]
                            # Keep the resolved family even if this answer only
                            # cited its short instructions. Never trust model IDs.
                            session.active_documents = (result.get("resolved_documents") or list(dict.fromkeys(
                                origin["document_key"] for source in result["sources"]
                                for origin in source["origins"])))[:3]
                        elif result["status"] == "clarification_needed":
                            session.history.append({"question": question, "answer": result["answer"]})
                            session.history = session.history[-3:]
                        elif result["status"] == "insufficient_context" and result.get("resolved_documents"):
                            # A readable question may follow an unreadable one.
                            # Keep backend-resolved file identity even when no
                            # supported answer could be committed for this turn.
                            session.active_documents = result["resolved_documents"][:3]
                            session.history.append({"question": question, "answer": result["answer"]})
                            session.history = session.history[-3:]
                        else:
                            # A missing/new topic still belongs in history; do
                            # not silently revert its next follow-up to old work.
                            session.history.append({"question": question, "answer": result["answer"]})
                            session.history = session.history[-3:]
                        if self.repository:
                            await asyncio.to_thread(self.repository.save_turn, session, turn_id, question, result)
                        committed = True
                    yield event
        except asyncio.CancelledError:
            raise
        except ConversationError as error:
            yield {"event": "error", "data": {"message": str(error)}}
        except (LLMProviderError, EmbeddingProviderError, rag_service.RAGValidationError):
            yield {"event": "error", "data": {"message": "The assistant could not complete a validated response. Please retry."}}
        except Exception:
            yield {"event": "error", "data": {"message": "The assistant is unavailable. Check the backend and retry."}}
        finally:
            if not committed:
                for key, value in previous.items():
                    setattr(session, key, value)
            session.busy = False
            session.updated_at = time.monotonic()
