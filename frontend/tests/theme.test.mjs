import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function load(context = {}) {
  const ctx = { exports: {}, ...context };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL("../lib/theme.ts", import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017 } }).outputText, ctx);
  return ctx.exports;
}

test("only light and dark are remembered; anything else follows the system", () => {
  const { normalizeTheme } = load();
  assert.equal(normalizeTheme("light"), "light");
  assert.equal(normalizeTheme("dark"), "dark");
  for (const bad of [null, undefined, "", "system", "DARK", "blue", 1, {}, ["dark"]]) assert.equal(normalizeTheme(bad), "system");
});

test("reading a blocked or corrupt store falls back to the system theme", () => {
  const blocked = load({ localStorage: { getItem() { throw new Error("blocked"); } } });
  assert.equal(blocked.readTheme(), "system");
  assert.equal(load({ localStorage: { getItem: () => "garbage" } }).readTheme(), "system");
  assert.equal(load({ localStorage: { getItem: () => "dark" } }).readTheme(), "dark");
});

test("saving stores light/dark, clears the key for system, and sets or removes data-theme", () => {
  const store = new Map(); const attrs = new Map();
  const api = load({
    localStorage: { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k) },
    document: { documentElement: { setAttribute: (k, v) => attrs.set(k, v), removeAttribute: k => attrs.delete(k) } },
  });
  api.saveTheme("dark");
  assert.equal(store.get(api.THEME_KEY), "dark"); assert.equal(attrs.get("data-theme"), "dark");
  api.saveTheme("light");
  assert.equal(store.get(api.THEME_KEY), "light"); assert.equal(attrs.get("data-theme"), "light");
  api.saveTheme("system");
  assert.equal(store.has(api.THEME_KEY), false); assert.equal(attrs.has("data-theme"), false);
});

test("saving still applies the theme when storage is blocked", () => {
  const attrs = new Map();
  const api = load({
    localStorage: { setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } },
    document: { documentElement: { setAttribute: (k, v) => attrs.set(k, v), removeAttribute: k => attrs.delete(k) } },
  });
  api.saveTheme("dark");
  assert.equal(attrs.get("data-theme"), "dark");
});

test("the pre-paint script applies only a valid saved theme and never throws", () => {
  const { THEME_INIT_SCRIPT } = load();
  const run = (stored, throws = false) => {
    const attrs = new Map();
    vm.runInNewContext(THEME_INIT_SCRIPT, {
      localStorage: { getItem: () => { if (throws) throw new Error("blocked"); return stored; } },
      document: { documentElement: { setAttribute: (k, v) => attrs.set(k, v) } },
    });
    return attrs.get("data-theme");
  };
  assert.equal(run("dark"), "dark");
  assert.equal(run("light"), "light");
  assert.equal(run("junk"), undefined);
  assert.equal(run(null), undefined);
  assert.equal(run("dark", true), undefined);
});
