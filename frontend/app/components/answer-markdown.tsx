import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { BLOCKED_ELEMENTS, prepareAnswerText } from "@/lib/answer-format";

// Single renderer for every assistant answer: new, streaming, saved, course-grounded and general.
// Raw HTML is dropped, links/images/forms are blocked, and KaTeX runs with trust disabled; a
// failing expression falls back to its readable source text instead of breaking the answer.
export default function AnswerMarkdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return <div className="conversation-markdown">
    <Markdown remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore", trust: false, errorColor: "currentColor" }]]}
      skipHtml disallowedElements={BLOCKED_ELEMENTS} unwrapDisallowed>
      {prepareAnswerText(text, { streaming })}
    </Markdown>
  </div>;
}
