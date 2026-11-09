/** The browser talks only to FastAPI; generation and citations stay on the backend. */
export type SourceLocation = {
  page_number?: number;
  slide_number?: number;
  block_number?: number;
  cell_number?: number;
  location?: string;
  start_character?: number;
  end_character?: number;
};
export type AssistantSource = {
  id: string;
  origins: {
    document_key: string;
    source_role?: string;
    references?: { source_type?: string; title?: string }[];
    title?: string;
    source_type?: string;
    url?: string | null;
    locations: SourceLocation[];
  }[];
};
export type AssistantResponse = {
  course_id: number;
  status: "answered" | "insufficient_context" | "needs_structured_data" | "structured_data_needed" | "indexing_needed" | "refused" | "clarification_needed";
  answer: string;
  answer_blocks: { text: string; source_ids: string[] }[];
  sources: AssistantSource[];
  warnings: string[];
  retrieval_status: string;
};

export type ConversationSummary = { conversation_id: string; course_id: number; title: string; created_at: string; updated_at: string };
export type SavedConversation = ConversationSummary & { messages: {
  message_id: string; turn_id: string; role: "user" | "assistant"; content: string; created_at: string; response: AssistantResponse | null;
}[] };

/** Mutate only a selected thread; never send messages or context with this action. */
export async function manageConversation(courseId: number, id: string, title?: string): Promise<ConversationSummary | void> {
  const response = await fetch(`http://127.0.0.1:8000/api/courses/${courseId}/conversations/${encodeURIComponent(id)}`, {
    method: title === undefined ? "DELETE" : "PATCH", cache: "no-store",
    ...(title === undefined ? {} : {headers:{"Content-Type":"application/json"},body:JSON.stringify({title:title.trim()})}),
  });
  if (!response.ok) throw new Error(response.status === 409 ? "A response is still running. Stop it or wait, then try again." : "Unable to update this conversation. Please retry.");
  const data=await response.json();
  if (data.course_id !== courseId || data.conversation_id !== id || (title === undefined ? data.deleted !== true : typeof data.title !== "string")) {
    throw new Error("Unable to verify the conversation update. Please reload history.");
  }
  if (title !== undefined) return data as ConversationSummary;
}

/** History stays on FastAPI/SQLite, scoped to the same account and course. */
export async function conversationHistory(courseId: number, id?: string, create = false, signal?: AbortSignal): Promise<ConversationSummary[] | SavedConversation> {
  const response = await fetch(`http://127.0.0.1:8000/api/courses/${courseId}/conversations${id ? `/${encodeURIComponent(id)}` : ""}`, {
    method: create ? "POST" : "GET", cache: "no-store", signal,
  });
  if (!response.ok) throw new Error("Unable to load conversation history. Please retry.");
  const data = await response.json();
  const records = Array.isArray(data) ? data : [data];
  if ((!id && !create && !Array.isArray(data)) || records.some((r: ConversationSummary) => !r || r.course_id !== courseId || typeof r.conversation_id !== "string" || typeof r.title !== "string")
    || ((id || create) && (Array.isArray(data) || !Array.isArray(data.messages) || data.messages.some((m: SavedConversation["messages"][number]) => !m || !["user", "assistant"].includes(m.role) || typeof m.content !== "string" || typeof m.turn_id !== "string") || (id && data.conversation_id !== id)))) {
    throw new Error("Conversation history is unavailable. Please retry.");
  }
  return data;
}

export async function askAssistant(courseId: number, question: string, signal?: AbortSignal): Promise<AssistantResponse> {
  const response = await fetch(`http://127.0.0.1:8000/api/courses/${courseId}/assistant`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  // Do not display upstream response bodies or backend implementation details.
  if (!response.ok) throw new Error("The assistant is unavailable. Please try again.");
  const data: AssistantResponse = await response.json();
  const statuses = ["answered", "insufficient_context", "needs_structured_data", "structured_data_needed", "indexing_needed", "refused", "clarification_needed"];
  if (data.course_id !== courseId || !statuses.includes(data.status) || typeof data.answer !== "string"
      || !Array.isArray(data.answer_blocks) || !Array.isArray(data.sources) || !Array.isArray(data.warnings)
      || typeof data.retrieval_status !== "string") {
    throw new Error("The assistant returned an unavailable response. Please try again.");
  }
  return data;
}

export function assistantStatusMessage(status: AssistantResponse["status"]) {
  switch (status) {
    case "insufficient_context": return "I couldn’t find enough information in the available course material to answer that.";
    case "needs_structured_data":
    case "structured_data_needed": return "This question requires current Canvas information, such as a deadline or submission status. Course documents cannot confirm those details. Check Canvas for the current information.";
    case "indexing_needed": return "Some requested material is not searchable yet. Use Update course search, then retry your question.";
    case "clarification_needed": return "Please clarify which assignment or topic you mean.";
    case "refused": return "The assistant could not answer this request. Try rephrasing your question.";
    default: return "";
  }
}

/** Only web links returned with a source may become clickable links. */
export function safeSourceUrl(value?: string | null) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
}

export function formatSourceLocation(location: SourceLocation) {
  const labels = [
    location.page_number != null ? `Page ${location.page_number}` : "",
    location.slide_number != null ? `Slide ${location.slide_number}` : "",
    location.block_number != null ? `Block ${location.block_number}` : "",
    location.cell_number != null ? `Cell ${location.cell_number}` : "",
    location.location || "",
  ].filter(Boolean);
  if (!labels.length && location.start_character != null && location.end_character != null) {
    labels.push(`Characters ${location.start_character}–${location.end_character}`);
  }
  return labels.join(" · ");
}

export type ConversationEvent =
  | { event: "session"; data: { conversation_id: string; turn_id: string; course_id: number } }
  | { event: "searching" | "generating"; data: Record<string, never> }
  | { event: "answer_delta"; data: { blocks: string[] } }
  | { event: "final"; data: AssistantResponse }
  | { event: "error"; data: { message: string } };

/** Parse actual SSE frames, including split UTF-8/network boundaries. */
export async function streamAssistant(courseId: number, question: string, turnId: string,
  conversationId: string | null, onEvent: (event: ConversationEvent) => void, signal: AbortSignal) {
  const response = await fetch(`http://127.0.0.1:8000/api/courses/${courseId}/assistant/stream`, {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ question, turn_id: turnId, conversation_id: conversationId }),
  });
  if (!response.ok || !response.body || !response.headers.get("content-type")?.includes("text/event-stream")) {
    throw new Error(response.status === 409 ? "This conversation expired or is busy. Start a new conversation or retry." : "The assistant is unavailable. Please retry.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "", final = false, sessionSeen = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replaceAll("\r\n", "\n");
      if (buffer.length > 2000000) throw new Error("The assistant stream exceeded its limit.");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        if (!frame || frame.startsWith(":")) continue;
        const kind = frame.split("\n").find((line) => line.startsWith("event:"))?.slice(6).trim();
        const raw = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        const data = JSON.parse(raw);
        if (final || !kind || !data || typeof data !== "object") throw new Error("Invalid assistant stream.");
        if (kind === "session") {
          if (sessionSeen || data.course_id !== courseId || data.turn_id !== turnId || typeof data.conversation_id !== "string") throw new Error("Invalid conversation response.");
          sessionSeen = true;
        } else if (!sessionSeen) throw new Error("Missing conversation response.");
        if (kind === "answer_delta" && (!Array.isArray(data.blocks) || data.blocks.some((text: unknown) => typeof text !== "string"))) throw new Error("Invalid answer stream.");
        if (kind === "final") {
          if (data.course_id !== courseId || !["answered", "insufficient_context", "needs_structured_data", "indexing_needed", "refused", "clarification_needed"].includes(data.status) || !Array.isArray(data.sources) || !Array.isArray(data.answer_blocks) || !Array.isArray(data.warnings) || typeof data.answer !== "string" || data.sources.some((source: AssistantSource) => typeof source.id !== "string" || !Array.isArray(source.origins) || source.origins.some((origin) => !Array.isArray(origin.locations))) || data.answer_blocks.some((block: {text: string; source_ids: string[]}) => typeof block.text !== "string" || !Array.isArray(block.source_ids))) throw new Error("Invalid final response.");
          final = true;
        }
        if (kind === "error") throw new Error("The response was interrupted or could not be validated. Please retry.");
        if (!["session", "searching", "generating", "answer_delta", "final"].includes(kind)) throw new Error("Unknown assistant stream event.");
        onEvent({ event: kind, data } as ConversationEvent);
      }
      if (done) break;
    }
    if (!final || buffer.trim()) throw new Error("The response was interrupted. Please retry.");
  } catch (error) {
    if (signal.aborted) throw error;
    throw new Error("The response was interrupted or could not be validated. Please retry.");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

/** Display only student-facing locations; internal offsets remain in the data. */
export function sourceLabel(source: AssistantSource["origins"][number], courseLabel: string) {
  if (source.source_role === "course_syllabus") return `${courseLabel} Syllabus`;
  const assignment = source.references?.find((reference) => reference.source_type === "assignment" && reference.title);
  return assignment?.title || source.title || "Course material";
}

export function studentSourceLocation(locations: SourceLocation[]) {
  const pages = [...new Set(locations.flatMap((loc) => loc.page_number != null ? [loc.page_number] : []))].sort((a, b) => a - b);
  const slides = [...new Set(locations.flatMap((loc) => loc.slide_number != null ? [loc.slide_number] : []))].sort((a, b) => a - b);
  return [pages.length ? `${pages.length === 1 ? "Page" : "Pages"} ${pages.join(", ")}` : "",
    slides.length ? `${slides.length === 1 ? "Slide" : "Slides"} ${slides.join(", ")}` : ""].filter(Boolean).join(" · ");
}
