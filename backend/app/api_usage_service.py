"""Local, account-scoped API estimates, not OpenAI billing or a spending cap."""
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

# Standard processing, USD per million tokens; checked 2026-11-03.
# https://developers.openai.com/api/docs/pricing
# https://developers.openai.com/api/docs/models/text-embedding-3-small
PRICING_VERSION = 'openai-standard-2026-11-03'
RATES = {
    'text-embedding-3-small': (0.02, 0, 0.02),
    'text-embedding-3-large': (0.13, 0, 0.13),
    'gpt-6-luna': (0.10, 0.50, 0.01),
    'gpt-6.1-sol': (2.0, 10.0, 0.10),
    'gpt-6.1-terra': (1.0, 5.0, 0.05),
    'gpt-6-astra': (10.0, 50.0, 1.0),
}


def _field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


class UsageRecorder:
    """Record once at the provider boundary, including calls later rejected by validation."""
    def __init__(self, store=None):
        self.store = store

    def record(self, response, *, feature, operation, model, request_id=None):
        from backend.app.embedding_service import default_store
        usage = _field(response, 'usage')
        incoming = _tokens(_field(usage, 'input_tokens', _field(usage, 'prompt_tokens')))
        outgoing = _tokens(_field(usage, 'output_tokens'))
        if feature == 'embedding' and incoming is not None:
            outgoing = 0
        cached = _tokens(_field(_field(usage, 'input_tokens_details'), 'cached_tokens')) or 0
        cached = min(cached, incoming or 0)
        rates = RATES.get(model)
        cost = None if rates is None or incoming is None or outgoing is None else (
            (incoming - cached) * rates[0] + outgoing * rates[1] + cached * rates[2]) / 1_000_000
        identity = _field(response, 'id') or request_id
        if not isinstance(identity, str) or not identity:
            identity = uuid.uuid4().hex
        entry = dict(operation_id=identity, recorded_at=datetime.now(timezone.utc).isoformat(),
                     feature=feature, operation=operation, model=model, input_tokens=incoming,
                     output_tokens=outgoing, cached_input_tokens=cached, estimated_cost_usd=cost,
                     pricing_version=PRICING_VERSION)
        try:
            (self.store or default_store()).record_api_usage(entry)
        except (sqlite3.Error, OSError, ValueError):
            # Accounting must not turn a valid answer into a failed conversation.
            logging.getLogger(__name__).warning('Local API usage could not be recorded.')


def usage_summary(store, *, now=None):
    """Aggregate recorded calls using the server's local calendar; old calls are not inferred."""
    local = (now or datetime.now().astimezone()).astimezone()
    rows = store.load_api_usage()
    def total(items):
        return {'estimated_cost_usd': round(sum(row['estimated_cost_usd'] or 0 for row in items), 8),
                'calls': len(items), 'unpriced_calls': sum(row['estimated_cost_usd'] is None for row in items),
                'input_tokens': sum(row['input_tokens'] or 0 for row in items),
                'output_tokens': sum(row['output_tokens'] or 0 for row in items)}
    today, month = [], []
    for row in rows:
        date = datetime.fromisoformat(row['recorded_at']).astimezone(local.tzinfo)
        if (date.year, date.month) == (local.year, local.month):
            month.append(row)
        if date.date() == local.date():
            today.append(row)
    all_time = total(rows)
    warning = store.load_settings()['monthly_warning_usd']
    return {'today': total(today), 'this_month': total(month), 'total': all_time,
            'monthly_breakdown': {
                'embedding': total([row for row in month if row['feature'] == 'embedding']),
                'vision': total([row for row in month if row['feature'] == 'vision']),
                'assistant_other': total([row for row in month if row['feature'] not in ('embedding', 'vision')]),
            },
            'embedding': total([row for row in rows if row['feature'] == 'embedding']),
            'vision': total([row for row in rows if row['feature'] == 'vision']),
            'assistant_other': total([row for row in rows if row['feature'] not in ('embedding', 'vision')]),
            'monthly_warning_usd': warning,
            'warning_reached': warning is not None and total(month)['estimated_cost_usd'] >= warning,
            'recording_started_at': rows[0]['recorded_at'] if rows else None,
            'timezone': str(local.tzinfo), 'pricing_version': PRICING_VERSION,
            'notice': "Estimated from API calls recorded by EasyCanvas. OpenAI's billing dashboard is the authoritative account total. Calls made before tracking was enabled or outside EasyCanvas are not included."}
