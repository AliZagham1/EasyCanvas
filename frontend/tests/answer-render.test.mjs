import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Transpile the real lib + component (no copies of the config) next to node_modules so imports resolve.
const dir = fs.mkdtempSync(path.join(path.resolve("tests"), ".render-"));
const emit = (src, out, fix = (s) => s) => fs.writeFileSync(path.join(dir, out), fix(ts.transpileModule(fs.readFileSync(src, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText));
emit("lib/answer-format.ts", "answer-format.mjs");
emit("app/components/answer-markdown.tsx", "answer-markdown.mjs", (s) => s.replace('"@/lib/answer-format"', '"./answer-format.mjs"')
  .replace('import "katex/dist/katex.min.css";', ""));
const { prepareAnswerText } = await import(pathToFileURL(path.join(dir, "answer-format.mjs")));
const AnswerMarkdown = (await import(pathToFileURL(path.join(dir, "answer-markdown.mjs")))).default;
test.after(() => fs.rmSync(dir, { recursive: true, force: true }));

const render = (text, streaming = false) => renderToStaticMarkup(React.createElement(AnswerMarkdown, { text, streaming }));

test("LaTeX matrix renders as KaTeX, not raw source", () => {
  const html = render("Q1: \\[ A = \\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix} \\]");
  assert.match(html, /class="katex-display"/);
  assert.doesNotMatch(html.replace(/<annotation[\s\S]*?<\/annotation>/g, ""), /begin\{bmatrix\}/);
  assert.match(html, /<mtable/);
  for (const n of ["1", "2", "3", "4"]) assert.ok(html.includes(`>${n}<`));
});

test("bare matrix environment without delimiters is recovered", () => {
  assert.match(render("Matrix: \\begin{pmatrix} 5 & 6 \\\\ 7 & 8 \\end{pmatrix}"), /class="katex-display"/);
});

test("inline math, fractions, exponents, Greek, roots", () => {
  const html = render("Let \\(\\alpha = \\frac{a}{b}\\), also $x^2 + \\sqrt{y}$ and \\[\\sum_{i=1}^{n} \\beta_i\\]");
  assert.equal((html.match(/class="katex"/g) || []).length, 3); // two inline + one inside the display block
  assert.match(html, /<mfrac/); assert.match(html, /<msqrt/); assert.match(html, /<msup/); assert.match(html, /α/); assert.match(html, /katex-display/);
});

test("currency dollars are not treated as math", () => {
  const html = render("It costs $5 and $10 in total.");
  assert.doesNotMatch(html, /katex/); assert.match(html, /\$5 and \$10/);
});

test("numbered questions with subparts stay separate items", () => {
  const html = render("1. First question\n   - (a) part a\n   - (b) part b\n2. Second question\n   - (a) again");
  assert.equal((html.match(/<ol>/g) || []).length, 1);
  assert.equal((html.match(/<ul>/g) || []).length, 2);
  assert.ok(html.indexOf("(a) part a") < html.indexOf("(b) part b") && html.indexOf("(b) part b") < html.indexOf("Second question"));
});

test("code blocks keep indentation and ignore math delimiters", () => {
  const html = render("```python\ndef f(x):\n    return \\(x\\) + $y$ <b>\n```\nUse `a < b`.");
  assert.match(html, /<pre><code[^>]*>def f\(x\):\n    return \\\(x\\\) \+ \$y\$ &lt;b&gt;/);
  assert.match(html, /<code>a &lt; b<\/code>/); assert.doesNotMatch(html, /katex/);
});

test("ordinary Markdown: headings, emphasis, tables", () => {
  const html = render("## Title\n\n**bold** and *italic*\n\n| a | b |\n|---|---|\n| 1 | 2 |");
  assert.match(html, /<h2>Title<\/h2>/); assert.match(html, /<strong>bold<\/strong>/); assert.match(html, /<table>/);
});

test("malformed math degrades to readable text without breaking the answer", () => {
  const html = render("Before \\[ \\frac{1}{ \\badmacro{ \\] after text stays.");
  assert.match(html, /after text stays/); assert.match(html, /Before/);
  assert.doesNotMatch(html, /<script/);
});

test("incomplete expression while streaming shows plain text; completes once closed", () => {
  const partial = render("Working: $$\\begin{bmatrix} 1 & 2", true);
  assert.doesNotMatch(partial, /katex-error/); assert.match(partial, /\$\$/);
  assert.match(prepareAnswerText("a $$x$$ b", { streaming: true }), /\$\$x\$\$/);
  assert.equal(render("$$x^2$$ done", false), render("$$x^2$$ done", true));
});

test("saved and streamed final text render identically", () => {
  const text = "1. Compute \\(A^{-1}\\)\n2. Solve \\[ Ax=b \\]";
  assert.equal(render(text, false), render(text, true));
});

test("citation markers are removed from prose but kept inside code", () => {
  assert.equal(prepareAnswerText("Fact [S1] and [S2, S3]."), "Fact  and .");
  assert.match(prepareAnswerText("`a[S1]`"), /a\[S1\]/);
});

test("unsafe HTML, scripts, links and images are not rendered", () => {
  const html = render("<script>alert(1)</script><img src=x onerror=alert(1)><b onclick=x>hi</b>\n\n[click](javascript:alert(1)) ![i](http://e/x.png)\n\n\\[ \\href{javascript:alert(1)}{x} \\]");
  assert.doesNotMatch(html, /<script|<img|onerror=|onclick=|<a[ >]|href=|src=/);
});

test("incomplete-material guidance renders as headed lists with titles kept literal", () => {
  const html = render("The retrieved course material is insufficient to answer this question.\n\n**What I found**\n\n- Retrieved material: `My_Lab_3.pdf`.\n\n**What is missing**\n\n- The handout does not list every command.\n\n**What I can still help with**\n\n- Explain one specific part.");
  assert.equal((html.match(/<strong>/g) || []).length, 3);
  assert.match(html, /<code>My_Lab_3\.pdf<\/code>/); assert.doesNotMatch(html, /<em>/);
  assert.equal((html.match(/<ul>/g) || []).length, 3);
});
