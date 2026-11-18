import type { Page } from "@playwright/test";

export type ConsentState = { status: "undecided" | "granted" | "declined" | "withdrawn"; enabled: boolean; needs_decision: boolean };
export const GRANTED: ConsentState = { status: "granted", enabled: true, needs_decision: false };

/** A stateful fake of /api/ai-consent. Returns the live state and the list of saved decisions. */
export async function mockConsent(page: Page, initial: ConsentState = GRANTED) {
  const state = { ...initial };
  const saved: string[] = [];
  await page.route("**/api/ai-consent", async route => {
    const request = route.request();
    if (request.method() === "PUT") {
      const { decision } = request.postDataJSON();
      saved.push(decision);
      Object.assign(state, { status: decision, enabled: decision === "granted", needs_decision: false });
    }
    await route.fulfill({ json: { ...state, decided_at: null, version: state.needs_decision ? null : 1, current_version: 1 } });
  });
  return { state, saved };
}
