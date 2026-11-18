"""One global AI Data Processing Consent per Canvas account on this installation.

Rules (docs/specs/ai-consent.md):
- Anything other than a saved, current-version "granted" decision means AI is DENIED: no record, a corrupt record,
  an older disclosure version, an unreadable database, or no store at all.
- Every code path that sends data to an external AI provider calls `require_ai_consent(store)` immediately before it
  does so. Canvas requests and local document processing never call it.
- `AIConsentRequired` deliberately does not subclass ValueError or LLMProviderError, so the broad `except` blocks that
  turn provider failures into retries or fallbacks cannot swallow it.
"""

import time
from datetime import datetime, timezone

# Bump when the disclosure changes materially; students must then agree again.
CONSENT_VERSION = 1
GRANTED, DECLINED, WITHDRAWN = "granted", "declined", "withdrawn"
DECISIONS = (GRANTED, DECLINED, WITHDRAWN)
ERROR_CODE = "ai_consent_required"
MESSAGE = "AI features are turned off. Enable AI Features to use this."
RECHECK_SECONDS = 0.5


class AIConsentRequired(Exception):
    """External AI processing was attempted without valid consent."""
    code = ERROR_CODE

    def __init__(self, message=MESSAGE):
        super().__init__(message)


def read_consent(store):
    """The saved record, or None when missing or malformed. Never raises."""
    if store is None:
        return None
    try:
        record = store.load_ai_consent()
    except Exception:  # noqa: BLE001 - an unreadable record must mean denied, not an error page.
        return None
    if not isinstance(record, dict):
        return None
    status, version = record.get("status"), record.get("version")
    if status not in DECISIONS or type(version) is not int:
        return None
    return {"status": status, "version": version, "decided_at": record.get("decided_at")}


def is_ai_enabled(store):
    record = read_consent(store)
    return bool(record and record["status"] == GRANTED and record["version"] == CONSENT_VERSION)


def require_ai_consent(store):
    """Call right before any external AI request. Unknown means denied."""
    if not is_ai_enabled(store):
        raise AIConsentRequired()


class ConsentWatch:
    """Re-check consent during a long operation (stream, batch loop) without hitting the database on every event."""

    def __init__(self, store, interval=RECHECK_SECONDS):
        self.store, self.interval, self._next = store, interval, time.monotonic() + interval

    def check(self):
        now = time.monotonic()
        if now >= self._next:
            self._next = now + self.interval
            require_ai_consent(self.store)


def consent_view(store):
    """What the API reports. `needs_decision` drives the first-visit modal."""
    record = read_consent(store)
    enabled = is_ai_enabled(store)
    stale = bool(record and record["version"] != CONSENT_VERSION)
    return {
        "status": record["status"] if record else "undecided",
        "enabled": enabled,
        "decided_at": record["decided_at"] if record else None,
        "version": record["version"] if record else None,
        "current_version": CONSENT_VERSION,
        # No valid decision for the current disclosure: ask. A declined or withdrawn decision stands until changed.
        "needs_decision": not record or stale,
    }


def record_decision(store, decision, version):
    if decision not in DECISIONS:
        raise ValueError("Unknown consent decision.")
    if version != CONSENT_VERSION:
        raise ValueError("The consent text has changed. Reload and review it again.")
    store.save_ai_consent({"status": decision, "version": CONSENT_VERSION,
                           "decided_at": datetime.now(timezone.utc).isoformat()})
    return consent_view(store)
