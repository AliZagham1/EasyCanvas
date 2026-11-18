/** Global AI Data Processing Consent. The backend is the source of truth; nothing here is stored in the browser. */
export type ConsentView = {
  status: "undecided" | "granted" | "declined" | "withdrawn";
  enabled: boolean;
  decided_at: string | null;
  version: number | null;
  current_version: number;
  /** True when there is no valid decision for the current disclosure: the first-visit modal shows. */
  needs_decision: boolean;
};
export type ConsentDecision = "granted" | "declined" | "withdrawn";
export type ConsentGate = "loading" | "enabled" | "disabled" | "unavailable";

const url = "http://127.0.0.1:8000/api/ai-consent";

export async function getConsent(signal?: AbortSignal): Promise<ConsentView> {
  const response = await fetch(url, { cache: "no-store", signal });
  if (!response.ok) throw new Error("AI settings are unavailable.");
  return parseConsent(await response.json());
}

export async function saveConsent(decision: ConsentDecision, version: number): Promise<ConsentView> {
  const response = await fetch(url, { method: "PUT", cache: "no-store", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, version }) });
  if (!response.ok) throw new Error(response.status === 409
    ? "The AI consent text changed. Reload the page and review it again."
    : "Your choice could not be saved. Please retry.");
  return parseConsent(await response.json());
}

/** Anything malformed is treated as an error, which the UI shows as AI off. Unknown never means enabled. */
export function parseConsent(value: unknown): ConsentView {
  const v = value as Partial<ConsentView> | null;
  if (!v || typeof v.enabled !== "boolean" || typeof v.needs_decision !== "boolean" || typeof v.current_version !== "number"
    || !["undecided", "granted", "declined", "withdrawn"].includes(v.status as string)) throw new Error("AI settings are unavailable.");
  return v as ConsentView;
}

export function consentGate(view: ConsentView | null, loading: boolean, error: boolean): ConsentGate {
  if (loading) return "loading";
  if (error || !view) return "unavailable";
  return view.enabled ? "enabled" : "disabled";
}

export function consentStatusLabel(view: ConsentView | null): string {
  if (!view) return "AI status unavailable";
  if (view.enabled) return "AI features are on";
  if (view.status === "withdrawn") return "AI features are off (consent withdrawn)";
  if (view.status === "declined") return "AI features are off";
  return "AI features are off (no choice made yet)";
}

export const WITHDRAWAL_NOTE = "Withdrawing stops EasyCanvas from sending anything new to AI services. It cannot recall data that was already sent to OpenAI, and it does not delete the course text and search data already saved on this computer. You can remove saved course materials under Course storage below.";
