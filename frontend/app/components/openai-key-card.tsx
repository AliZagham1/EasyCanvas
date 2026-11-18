"use client";
import { useEffect, useRef, useState } from "react";
import { API_KEY_CHANGED_EVENT, describeApiKey, getApiKeyStatus, removeApiKey, saveApiKey } from "@/lib/search-index";
import type { ApiKeyStatus } from "@/lib/search-index";
import { AIOffNotice } from "./ai-consent-provider";

/** Lets a student bring their own OpenAI key. The key is sent once, never shown again, and never read back. */
export default function OpenAIKeyCard() {
  const [status, setStatus] = useState<ApiKeyStatus | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const busyRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    getApiKeyStatus(controller.signal).then(setStatus).catch(() => {
      if (!controller.signal.aborted) setError("Unable to load the API key status. Check the backend and retry.");
    });
    return () => controller.abort();
  }, []);

  async function run(action: () => Promise<ApiKeyStatus>, success: string) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(""); setMessage("");
    try {
      setStatus(await action());
      setValue("");                                   // the key is never kept in the page after sending
      setMessage(success);
      window.dispatchEvent(new Event(API_KEY_CHANGED_EVENT));
    } catch (e) { setError(e instanceof Error ? e.message : "The API key request failed."); }
    finally { busyRef.current = false; setBusy(false); }
  }

  return <section className="api-key-card" aria-labelledby="api-key-title">
    <h2 id="api-key-title" className="section-title">OpenAI API key</h2>
    <AIOffNotice>AI features are off, so a key cannot be saved or checked yet.</AIOffNotice>
    <p className="assistant-hint" role="status">{describeApiKey(status)}</p>
    {error && <p className="coverage-notice" role="alert">{error}</p>}
    {message && <p className="assistant-hint search-save-status" role="status">{message}</p>}
    <form className="api-key-form" onSubmit={event => { event.preventDefault(); void run(() => saveApiKey(value.trim()), "Key verified and saved. Embeddings, the Study Assistant and Vision now use it."); }}>
      <label htmlFor="openai-api-key">Use your own key</label>
      <div>
        <input id="openai-api-key" type="password" autoComplete="off" spellCheck={false} autoCapitalize="off" placeholder="sk-..."
          value={value} onChange={event => setValue(event.target.value)} disabled={busy} />
        <button type="submit" className="primary-button" disabled={busy || !value.trim()}>{busy ? "Verifying..." : "Save and verify"}</button>
        {status?.source === "saved" && <button type="button" className="primary-button" disabled={busy}
          onClick={() => void run(removeApiKey, "Saved key removed. EasyCanvas is using the backend .env key, if there is one.")}>Remove saved key</button>}
      </div>
    </form>
    <p className="assistant-hint">One key powers embeddings (search), the Study Assistant and Vision. It is checked with OpenAI using a free request, saved only in this computer&apos;s local database (not encrypted), and never sent back to your browser. Usage is billed to your OpenAI account, and EasyCanvas tracks each key&apos;s usage separately.</p>
  </section>;
}
