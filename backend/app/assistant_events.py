"""Durable, privacy-safe diagnostics for the Study Assistant's model calls.

Events say *what* happened (stage, outcome, error class, HTTP status, attempt), never what was asked or answered.
Recording must never break a student's question, so every failure here is swallowed.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

OUTCOMES = ("retry", "failed", "recovered", "fallback", "rejected")


def record(store, stage, outcome, *, error=None, attempt=None, model=None):
    """Save one event. `error` may be an exception; only its class name and HTTP status are kept."""
    if store is None:
        return
    try:
        store.record_assistant_event({
            "recorded_at": datetime.now(timezone.utc).isoformat(), "stage": stage, "outcome": outcome,
            "error_class": type(error).__name__ if error is not None else None,
            "status_code": getattr(error, "status_code", None) if error is not None else None,
            "attempt": attempt, "model": model})
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError):
        pass


def summarize(store, hours=24, now=None):
    """Counts by stage, outcome and error class over the last `hours`, plus the share of understanding calls that failed."""
    now = now or datetime.now(timezone.utc)
    events = store.load_assistant_events((now - timedelta(hours=hours)).isoformat())
    counts = {}
    for event in events:
        key = (event["stage"], event["outcome"], event["error_class"])
        counts[key] = counts.get(key, 0) + 1
    rows = [{"stage": stage, "outcome": outcome, "error_class": error_class, "count": count}
            for (stage, outcome, error_class), count in sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))]
    return {"hours": hours, "total_events": len(events), "events": rows,
            "recent": [{key: event[key] for key in ("recorded_at", "stage", "outcome", "error_class", "status_code", "attempt")}
                       for event in events[-20:]]}
