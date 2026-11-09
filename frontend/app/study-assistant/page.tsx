"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { getCourses } from "@/lib/coursework";
import type { Course } from "@/lib/coursework";
import { formatCourseCode } from "@/lib/course-label";
import { streamAssistant, conversationHistory } from "@/lib/assistant";
import type { ConversationSummary, SavedConversation } from "@/lib/assistant";
import AssistantMessage from "../components/assistant-message";
import type { ChatTurn } from "../components/assistant-message";
import ConversationActions from "../components/conversation-actions";
import CourseSearchStatus from "../components/course-search-status";

export default function StudyAssistantPage() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [coursesLoading, setCoursesLoading] = useState(true);
  const [coursesError, setCoursesError] = useState(false);
  const [courseId, setCourseId] = useState("");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [managing, setManaging] = useState(false);
  const historyRequest = useRef<AbortController | null>(null);
  const request = useRef<AbortController | null>(null);
  const session = useRef<string | null>(null);
  const thread = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    getCourses(controller.signal).then((data) => {
      if (!controller.signal.aborted) setCourses(data);
    }).catch(() => {
      if (!controller.signal.aborted) setCoursesError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setCoursesLoading(false);
    });
    return () => { controller.abort(); request.current?.abort(); historyRequest.current?.abort(); };
  }, []);

  useEffect(() => {
    if (followBottom.current && thread.current) thread.current.scrollTop = thread.current.scrollHeight;
  }, [turns]);

  function restore(saved: SavedConversation) {
    session.current = saved.conversation_id; setConversationId(saved.conversation_id);
    setTurns(saved.messages.filter((m) => m.role === "user").map((m) => {
      const reply = saved.messages.find((a) => a.turn_id === m.turn_id && a.role === "assistant");
      return { id: m.turn_id, question: m.content, draft: [], phase: reply?.response ? "complete" : "error",
        response: reply?.response || undefined, error: reply?.response ? undefined : "This response was interrupted. You can retry the question." };
    }));
  }
  async function openConversation(nextCourse: string, id?: string, create = false) {
    request.current?.abort(); request.current = null; historyRequest.current?.abort();
    const controller = new AbortController(); historyRequest.current = controller;
    setCourseId(nextCourse); setPending(false); setQuestion(""); setNotice(""); setHistoryError(""); setHistoryLoading(true);
    session.current = null; setConversationId(""); setTurns([]); setConversations([]);
    try {
      if (!nextCourse) return;
      let list = await conversationHistory(Number(nextCourse), undefined, false, controller.signal) as ConversationSummary[];
      const target = id || (!create && list[0]?.conversation_id);
      const saved = await conversationHistory(Number(nextCourse), target || undefined, !target, controller.signal) as SavedConversation;
      if (historyRequest.current !== controller) return;
      if (!list.some((c) => c.conversation_id === saved.conversation_id)) list = [saved, ...list];
      setConversations(list); restore(saved); followBottom.current = true;
    } catch {
      if (!controller.signal.aborted) setHistoryError("Unable to load conversation history. Your saved conversations have not been deleted. Please retry.");
    } finally {
      if (historyRequest.current === controller) { historyRequest.current = null; setHistoryLoading(false); }
    }
  }
  function update(id: string, patch: Partial<ChatTurn>) {
    setTurns((items) => items.map((item) => item.id === id ? { ...item, ...patch } : item));
  }
  async function send(text: string, retryId?: string) {
    if (request.current || historyLoading || managing || !session.current || !courseId || !text.trim() || new TextEncoder().encode(text).length > 8000) return;
    const controller = new AbortController(); request.current = controller;
    const submittedSession = session.current;
    const id = retryId || crypto.randomUUID();
    if (retryId) update(id, { phase: "searching", draft: [], error: undefined });
    else setTurns((items) => [...items, { id, question: text.trim(), draft: [], phase: "searching" }]);
    followBottom.current = true; setQuestion(""); setPending(true); setNotice("Searching course material...");
    try {
      await streamAssistant(Number(courseId), text.trim(), id, session.current, (event) => {
        if (request.current !== controller) return;
        if (event.event === "session") { session.current = event.data.conversation_id; setConversationId(event.data.conversation_id); }
        if (event.event === "generating") { update(id, { phase: "generating" }); setNotice("Generating answer..."); }
        if (event.event === "answer_delta") update(id, { draft: event.data.blocks });
        if (event.event === "final") {
          update(id, { phase: "complete", response: event.data, draft: [] }); setNotice("Response complete.");
          void conversationHistory(Number(courseId), undefined, false, controller.signal).then((list) => {
            if (session.current === submittedSession && Array.isArray(list)) setConversations(list);
          }).catch(() => {});
        }
      }, controller.signal);
    } catch (error) {
      if (request.current !== controller) return;
      update(id, { phase: "error", draft: [], response: undefined,
        error: controller.signal.aborted ? "Response stopped. You can retry this question." : error instanceof Error ? error.message : "Unable to complete the response. Please retry." });
      setNotice(controller.signal.aborted ? "Response stopped." : "Response interrupted. Retry is available.");
    } finally {
      if (request.current === controller) { request.current = null; setPending(false); input.current?.focus(); }
    }
  }
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); void send(question); }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(question); }
  }
  const current = courses.find((course) => String(course.id) === courseId);
  const courseLabel = formatCourseCode(current?.course_code, current?.name || "Course");
  const tooLong = new TextEncoder().encode(question).length > 8000;
  const selectedConversation = conversations.find(c=>c.conversation_id===conversationId);

  return <div className="page-content conversation-page">
    <header className="page-header conversation-header"><div><h1 className="page-title">Study Assistant</h1><p className="page-description">Ask questions using your Canvas course material.</p></div>
      <button className="primary-button" onClick={() => { void openConversation(courseId, undefined, true); }} disabled={!courseId || pending || historyLoading || managing}>New conversation</button>
    </header>
    <div className="conversation-course">
      <label htmlFor="assistant-course">Course</label>
      <select id="assistant-course" value={courseId} disabled={managing || coursesLoading || coursesError || !courses.length} onChange={(event) => { void openConversation(event.target.value); }}>
        <option value="">{coursesLoading ? "Loading courses..." : "Select a course"}</option>
        {courses.map((course) => <option key={course.id} value={course.id}>{formatCourseCode(course.course_code, course.name)} — {course.name}</option>)}
      </select>
      {courseId && <><label htmlFor="assistant-conversation">Conversation</label>
        <select id="assistant-conversation" value={conversationId} disabled={managing || historyLoading || pending || !conversations.length} onChange={(event) => { void openConversation(courseId, event.target.value); }}>
          {!conversations.length && <option value="">{historyLoading ? "Loading conversations..." : "Conversation unavailable"}</option>}
          {conversations.map((c) => <option key={c.conversation_id} value={c.conversation_id}>{c.title}</option>)}
        </select></>}
      {selectedConversation && <ConversationActions key={selectedConversation.conversation_id} courseId={Number(courseId)}
        conversation={selectedConversation} disabled={pending || historyLoading || managing} onBusyChange={setManaging}
        onRename={updated=>setConversations(items=>items.map(c=>c.conversation_id===updated.conversation_id ? {...c,...updated} : c))}
        onDelete={async id=>{
          setConversations(items=>items.filter(c=>c.conversation_id!==id));
          if(session.current===id) {session.current=null;setConversationId("");setTurns([]);await openConversation(courseId);}
        }} />}
      {historyError && <p className="assistant-hint" role="alert">{historyError} <button className="text-link" onClick={() => { void openConversation(courseId); }}>Retry history</button></p>}
      {coursesError && <p className="assistant-hint" role="alert">Unable to load courses. Check the backend connection and reload this page.</p>}
      {!coursesLoading && !coursesError && !courses.length && <p className="assistant-hint">No current courses were found in Canvas.</p>}
    </div>
    {courseId && <CourseSearchStatus key={courseId} courseId={Number(courseId)} />}
    <div className="conversation-thread" ref={thread} aria-label="Conversation" role="region" onScroll={() => {
      const element = thread.current; if (element) followBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
    }}>
      {historyLoading ? <p className="assistant-hint" role="status">Loading conversation...</p> : !turns.length && <div className="conversation-empty"><h2>{courseId ? `Ask about ${courseLabel}` : "Start with a course"}</h2><p>{courseId ? "Explore an assignment, understand a concept, or ask about course policies. Follow-up questions stay connected to this conversation." : "Select a course above to ask questions grounded in its saved material."}</p></div>}
      {turns.map((turn) => <AssistantMessage key={turn.id} turn={turn} courseLabel={courseLabel} busy={pending || turn.id !== turns.at(-1)?.id} retry={() => { void send(turn.question, turn.id); }} />)}
    </div>
    <form className="conversation-composer" onSubmit={submit} aria-label="Send a course question">
      <label htmlFor="assistant-question">{turns.length ? "Follow-up question" : "Question"}</label>
      <textarea id="assistant-question" ref={input} rows={2} value={question} maxLength={8000} disabled={managing || !courseId || historyLoading || !!historyError}
        onChange={(event) => setQuestion(event.target.value)} onKeyDown={keyDown}
        placeholder={turns.length ? "Ask a follow-up question..." : "Ask about an assignment, lecture, or course policy..."}
        aria-describedby="composer-hint" aria-invalid={tooLong || undefined} />
      <div className="conversation-composer-footer"><p id="composer-hint" className="assistant-hint">{tooLong ? "Please shorten your question." : "Enter to send · Shift+Enter for a new line"}</p>
        {pending ? <button className="primary-button" type="button" onClick={() => request.current?.abort()}>Stop response</button>
          : <button className="primary-button" type="submit" disabled={managing || !courseId || historyLoading || !!historyError || !question.trim() || tooLong}>Send</button>}
      </div>
      <p className="assistant-hint conversation-disclaimer">Answers use saved course material. Check Canvas for current deadlines and submission status.</p>
    </form>
    <p className="conversation-announcement" role="status" aria-live="polite">{notice}</p>
  </div>;
}
