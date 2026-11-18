"use client";

import { useState } from "react";
import { consentStatusLabel, WITHDRAWAL_NOTE } from "@/lib/ai-consent";
import { useAIConsent } from "./ai-consent-provider";

/** Settings → AI & Privacy: see the current choice, enable AI, or withdraw consent. */
export default function AIPrivacyCard() {
  const { view, gate, openConsent, decide, refresh } = useAIConsent();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function withdraw() {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await decide("withdrawn"); setConfirming(false); setMessage("Consent withdrawn. EasyCanvas will not send anything new to AI services."); }
    catch (e) { setError(e instanceof Error ? e.message : "Consent could not be withdrawn. Please retry."); }
    finally { setBusy(false); }
  }

  return <section className="api-key-card" aria-labelledby="ai-privacy-title">
    <h2 id="ai-privacy-title" className="section-title">AI &amp; Privacy</h2>
    <p className="assistant-hint" role="status">{gate === "loading" ? "Checking AI settings..." : consentStatusLabel(view)}</p>
    {gate === "unavailable" && <p className="coverage-notice" role="alert">AI settings could not be loaded, so AI stays off.{" "}
      <button type="button" className="text-link" onClick={() => void refresh()}>Retry</button></p>}
    {error && <p className="coverage-notice" role="alert">{error}</p>}
    {message && <p className="assistant-hint search-save-status" role="status">{message}</p>}
    <p className="assistant-hint">When AI is on, EasyCanvas sends assignment instructions, course documents, page images, and your Study Assistant questions to external AI services, including OpenAI, to build search, read images, and write explanations. This choice covers all your current and future Canvas courses. Dashboard, Upcoming, Priorities, Courses, Class Schedule and Canvas Sync work the same either way.</p>
    {gate === "disabled" && <button type="button" className="primary-button btn-primary" onClick={openConsent}>Enable AI Features</button>}
    {gate === "enabled" && !confirming && <button type="button" className="primary-button" onClick={() => { setMessage(""); setConfirming(true); }}>Withdraw consent</button>}
    {gate === "enabled" && confirming && <div role="group" aria-label="Confirm withdrawal">
      <p className="assistant-hint">{WITHDRAWAL_NOTE}</p>
      <div className="conversation-dialog-footer">
        <button type="button" className="primary-button" disabled={busy} onClick={() => setConfirming(false)}>Keep AI on</button>
        <button type="button" className="primary-button btn-danger conversation-delete-button" disabled={busy} onClick={() => void withdraw()}>Withdraw consent</button>
      </div></div>}
    {gate !== "enabled" && <p className="assistant-hint">{WITHDRAWAL_NOTE}</p>}
  </section>;
}
