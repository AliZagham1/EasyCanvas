import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { assistantStatusMessage } from "@/lib/assistant";
import type { AssistantResponse } from "@/lib/assistant";
import AssistantSources from "./assistant-sources";

export type ChatTurn = {
  id: string; question: string; draft: string[]; phase: "searching" | "generating" | "complete" | "error";
  response?: AssistantResponse; error?: string;
};

export default function AssistantMessage({ turn, courseLabel, retry, busy }: {
  turn: ChatTurn; courseLabel: string; retry: () => void; busy: boolean;
}) {
  const final = turn.response;
  const blocks = final?.status === "answered" ? final.answer_blocks.map((block) => block.text) : turn.draft;
  return <article className="conversation-turn" aria-label="Question and response">
    <section className="conversation-user" aria-label="Your question"><h2>You</h2><p>{turn.question}</p></section>
    <section className="conversation-assistant" aria-label="EasyCanvas response" aria-busy={turn.phase === "searching" || turn.phase === "generating"}>
      <h2>EasyCanvas</h2>
      {turn.phase === "error" ? <div className="conversation-error" role="alert"><p>{turn.error}</p><button className="text-link" onClick={retry} disabled={busy}>Retry response</button></div>
        : final && final.status !== "answered" ? <p>{final.answer || assistantStatusMessage(final.status)}</p>
        : blocks.length ? blocks.map((text, index) => <div className="conversation-answer-block" key={index}>
          <div className="conversation-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml
            allowedElements={["p", "strong", "em", "h2", "h3", "h4", "ul", "ol", "li", "pre", "code", "blockquote", "br", "hr", "table", "thead", "tbody", "tr", "th", "td", "del"]} unwrapDisallowed>
            {text.replace(/\[S\d+(?:\s*[,;]\s*S\d+)*\]/g, "")}
          </Markdown></div>
          {final?.status === "answered" && <div className="conversation-citations" aria-label="Sources for this passage">
            {[...new Set(final.answer_blocks[index]?.source_ids || [])].filter((id) => final.sources.some((source) => source.id === id)).map((id) =>
              <a className="text-link" key={id} href={`#${turn.id}-${id}`} onClick={() => {
                const target = document.getElementById(`${turn.id}-${id}`);
                const details = target?.closest("details"); if (details) details.open = true;
                requestAnimationFrame(() => { target?.scrollIntoView({ block: "nearest" }); target?.focus({ preventScroll: true }); });
              }}>[{id}]</a>)}
          </div>}
        </div>) : <p className="assistant-hint" role="status">{turn.phase === "searching" ? "Searching course material..." : "Generating answer..."}</p>}
      {turn.phase === "generating" && blocks.length > 0 && <p className="conversation-progress assistant-hint" role="status">Generating answer — sources will appear after validation.</p>}
      {final?.retrieval_status === "partial" && <p className="assistant-hint">Some material is not indexed. This answer uses the available indexed content.</p>}
      {final && final.warnings.length > 0 && <details className="assistant-notes"><summary>Material limitations</summary><ul>{final.warnings.map((warning, i) => <li key={i}>{warning === "images_not_extracted" ? "Images in the material were not read." : warning.replaceAll("_", " ")}</li>)}</ul></details>}
      {final && <AssistantSources sources={final.sources} messageId={turn.id} courseLabel={courseLabel} />}
    </section>
  </article>;
}
