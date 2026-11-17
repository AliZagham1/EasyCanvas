// Pure, dependency-free preparation of assistant answer text before Markdown/KaTeX rendering.
// It only changes math *delimiters* and removes citation markers; it never edits content inside math.

const CITATION = /\[S\d+(?:\s*[,;]\s*S\d+)*\]/g;
const BARE_ENV = /\\begin\{(bmatrix|pmatrix|vmatrix|Vmatrix|Bmatrix|matrix|cases|aligned|align\*?|array|gather\*?)\}[\s\S]*?\\end\{\1\}/g;

// Split into fenced/inline code (left untouched) and prose (normalized).
function mapProse(text: string, fn: (prose: string) => string): string {
  return text.split(/(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g).map((part, i) => (i % 2 ? part : fn(part))).join("");
}

function normalizeMath(prose: string): string {
  let out = prose
    // \[ ... \]  ->  $$ block
    .replace(/\\\[([\s\S]+?)\\\]/g, (_m, body: string) => `\n\n$$\n${body.trim()}\n$$\n\n`)
    // \( ... \)  ->  inline $$..$$ (remark-math treats it as inline inside a paragraph)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_m, body: string) => `$$${body.trim()}$$`);
  // Bare environments written without any delimiter: wrap, but only when not already inside $...$.
  out = out.split(/(\$\$[\s\S]*?\$\$)/g).map((seg, i) => (i % 2 ? seg
    : seg.replace(BARE_ENV, (m) => `\n\n$$\n${m}\n$$\n\n`))).join("");
  // Single-dollar inline math -> $$..$$, skipping currency such as "$5 and $10".
  out = out.split(/(\$\$[\s\S]*?\$\$)/g).map((seg, i) => (i % 2 ? seg
    : seg.replace(/(?<![\\$\w])\$(?![\s$\d])([^$\n]+?)(?<![\s\\])\$(?![\d$])/g, (_m, body: string) => `$$${body}$$`))).join("");
  return out;
}

/**
 * Prepare answer text for rendering.
 * `streaming` hides an unfinished `$$` so a half-written expression shows as plain text instead of a render error.
 */
export function prepareAnswerText(text: string, options: { streaming?: boolean } = {}): string {
  let out = mapProse(text, (p) => normalizeMath(p.replace(CITATION, "")));
  if (options.streaming) {
    const marked = mapProse(out, (p) => p.replace(/\$\$/g, "\u0001"));
    if ((marked.match(/\u0001/g) || []).length % 2 === 1) {
      const last = marked.lastIndexOf("\u0001");
      out = (marked.slice(0, last) + "\u0002" + marked.slice(last + 1))
        .replace(/\u0001/g, () => "$$").replace(/\u0002/g, () => "\\$\\$");
    }
  }
  return out;
}

export const BLOCKED_ELEMENTS = ["script", "style", "iframe", "object", "embed", "form", "input", "button", "textarea", "select", "img", "video", "audio", "link", "meta", "base", "a"];
