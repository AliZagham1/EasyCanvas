"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { consentGate, getConsent, saveConsent } from "@/lib/ai-consent";
import type { ConsentDecision, ConsentGate, ConsentView } from "@/lib/ai-consent";
import AIConsentModal from "./ai-consent-modal";

type ConsentContext = {
  view: ConsentView | null;
  gate: ConsentGate;
  enabled: boolean;
  refresh: () => Promise<void>;
  decide: (decision: ConsentDecision) => Promise<void>;
  openConsent: () => void;
};

const Context = createContext<ConsentContext | null>(null);

export function useAIConsent(): ConsentContext {
  const value = useContext(Context);
  if (!value) throw new Error("useAIConsent must be used inside AIConsentProvider.");
  return value;
}

/** Loads the saved decision from the backend and owns the consent modal. Non-AI pages never read it. */
export function AIConsentProvider({ children }: { children: ReactNode }) {
  const [view, setView] = useState<ConsentView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [forced, setForced] = useState(false);       // opened by "Enable AI Features"
  const [dismissed, setDismissed] = useState(false); // Escape on the first-visit modal: ask again next visit, save nothing
  const requests = useRef(0);

  const refresh = useCallback(async () => {
    const id = ++requests.current;
    try {
      const next = await getConsent();
      if (id === requests.current) { setView(next); setError(false); }
    } catch {
      if (id === requests.current) { setView(null); setError(true); }
    } finally {
      if (id === requests.current) setLoading(false);
    }
  }, []);

  useEffect(() => { const timer = setTimeout(() => void refresh(), 0); return () => clearTimeout(timer); }, [refresh]);

  const decide = useCallback(async (decision: ConsentDecision) => {
    const version = view?.current_version;
    if (version === undefined) throw new Error("AI settings are unavailable.");
    const next = await saveConsent(decision, version);
    requests.current++;
    setView(next); setError(false); setForced(false);
  }, [view]);

  const value = useMemo<ConsentContext>(() => ({
    view, gate: consentGate(view, loading, error), enabled: !loading && !error && !!view?.enabled,
    refresh, decide, openConsent: () => { setDismissed(false); setForced(true); },
  }), [view, loading, error, refresh, decide]);

  const open = !loading && !error && !!view && (forced || (view.needs_decision && !dismissed));
  return <Context.Provider value={value}>
    {children}
    {open && view && <AIConsentModal firstVisit={view.needs_decision} onDecide={decide}
      onClose={() => { setForced(false); setDismissed(true); }} />}
  </Context.Provider>;
}

/** One line shown where an AI-only control would otherwise fail without explanation. */
export function AIOffNotice({ children }: { children?: ReactNode }) {
  const { gate, openConsent } = useAIConsent();
  if (gate === "enabled" || gate === "loading") return null;
  return <p className="coverage-notice ai-off-notice" role="status">
    {children ?? "AI features are off, so this does not work yet."}{" "}
    {gate === "disabled" && <button type="button" className="text-link" onClick={openConsent}>Enable AI Features</button>}
  </p>;
}
