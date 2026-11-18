"use client";

import { useEffect, useRef, useState } from "react";
import type { ConsentDecision } from "@/lib/ai-consent";

/**
 * The disclosure shown before any AI processing. Two equal choices, neither preselected.
 * Escape closes without saving a decision. Wording must match what the backend actually sends (docs/specs/ai-consent.md);
 * changing it materially requires bumping CONSENT_VERSION in backend/app/consent_service.py.
 */
export default function AIConsentModal({ firstVisit, onDecide, onClose }: {
  firstVisit: boolean; onDecide: (decision: ConsentDecision) => Promise<void>; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    return () => element?.close();
  }, []);

  async function choose(decision: ConsentDecision) {
    if (pending) return;
    setPending(true); setError("");
    try {
      // "Not Now" on the first visit records a decision so the modal is not shown on every visit.
      // When a decision already exists (opened from "Enable AI Features"), it simply closes.
      if (decision === "declined" && !firstVisit) onClose();
      else await onDecide(decision);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Your choice could not be saved. Please retry.");
    } finally { setPending(false); }
  }

  return <dialog ref={dialog} className="consent-dialog" aria-labelledby="consent-title" aria-describedby="consent-intro"
    onCancel={event => { event.preventDefault(); if (!pending) onClose(); }}
    onKeyDown={event => {
      if (event.key !== "Tab") return;
      const controls = event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
      const first = controls[0], last = controls[controls.length - 1];
      if (!first) event.preventDefault();
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }}>
    <h2 id="consent-title">AI Data Processing Consent</h2>
    <div className="consent-body">
      <p id="consent-intro">EasyCanvas helps you organize your coursework and understand educational materials using AI-powered study features.</p>
      <h3>How your data is used</h3>
      <p>When you enable AI features, EasyCanvas may send relevant content from your current and future Canvas courses to external AI services, including OpenAI.</p>
      <p>This may include assignment instructions, lecture materials, course documents, and questions you ask the Study Assistant.</p>
      <p>External AI services may be used to generate embeddings, interpret images, and produce educational explanations.</p>
      <h3>Your choice</h3>
      <p>AI features are optional. You can continue using the Dashboard, Upcoming Assignments, Priorities, and other non-AI features without enabling AI processing.</p>
      <p>You can withdraw your consent at any time through Settings. Withdrawing stops new AI processing; it cannot recall data already sent.</p>
      <p>Your instructor&apos;s AI-use rules still apply, and AI-generated explanations may contain mistakes.</p>
      <h3>Consent statement</h3>
      <p>By selecting Agree &amp; Continue, you authorize the AI processing described above for your current and future Canvas courses.</p>
    </div>
    {error && <p role="alert" className="conversation-dialog-error">{error}</p>}
    <div className="conversation-dialog-footer">
      <button type="button" className="primary-button" disabled={pending} onClick={() => void choose("declined")}>Not Now</button>
      <button type="button" className="primary-button btn-primary" disabled={pending} onClick={() => void choose("granted")}>Agree &amp; Continue</button>
    </div>
    <p className="assistant-hint consent-footnote">Not Now: continue without AI.</p>
  </dialog>;
}
