"""Persist and resume incremental course indexing in the existing sync worker."""
from datetime import datetime, timedelta, timezone

from backend.app.embedding_service import EmbeddingConfiguration, default_store, embed_course


def index_status(course_id, *, store=None, configuration=None):
    """Count current eligible passages, never stale vectors or removed sources."""
    store = store or default_store()
    configuration = configuration or EmbeddingConfiguration()
    snapshot = store.load_course(course_id)
    if not snapshot or snapshot.get('materials_removed'):
        raise ValueError('Saved course materials not found.')
    chunks = store.load_chunks(course_id, eligible_only=True)
    cached = store.load_embeddings(course_id, configuration)
    dates = store.embedding_dates(course_id, configuration)
    job = store.load_index_job(course_id)
    failed = set(job.get('failed_hashes', [])) if job.get('config_hash') == configuration.fingerprint else set()
    def counts(selected):
        total = len(selected)
        indexed = sum(chunk['text_hash'] in cached for chunk in selected)
        failures = sum(chunk['text_hash'] in failed and chunk['text_hash'] not in cached for chunk in selected)
        last = max((dates[chunk['text_hash']] for chunk in selected if chunk['text_hash'] in dates), default=None)
        return {'eligible_chunks': total, 'indexed_chunks': indexed,
                'pending_chunks': total - indexed - failures, 'failed_chunks': failures,
                'remaining_chunks': total - indexed,
                'last_successful_at': last if total and indexed == total else None}
    result = counts(chunks)
    # Include readable documents with no eligible text too, so 0/0 does not imply a readable file.
    documents = []
    for document in store.load_documents(course_id):
        key = document['document_key']
        documents.append({'document_key': key, 'title': document.get('title'),
                          **counts([chunk for chunk in chunks if chunk['document_key'] == key]),
                          'extraction_status': document.get('status')})
    matching = job.get('config_hash') == configuration.fingerprint
    job_status = job.get('status', 'idle') if matching else 'idle'
    if snapshot.get('ingestion_status') == 'running':
        state = 'syncing'
    elif not result['eligible_chunks']:
        state = 'empty'
    elif not result['remaining_chunks']:
        state = 'ready'
    elif job_status in ('queued', 'running'):
        state = 'updating'
    elif result['failed_chunks'] or job_status == 'failed':
        state = 'failed'
    else:
        state = 'pending'
    metadata = snapshot.get('course', {}).get('metadata', {})
    unique = {chunk['text_hash'] for chunk in chunks}
    return {'course_id': course_id, 'title': metadata.get('course_code') or metadata.get('name') or str(course_id),
            **result, 'unique_inputs': len(unique), 'indexed_inputs': len(unique & cached.keys()),
            'status': state, 'job_status': job_status, 'automatic': store.load_settings()['auto_index'],
            'last_successful_at': (job.get('last_successful_at') if matching else None) or result['last_successful_at'],
            'error': job.get('error') if matching else None, 'documents': documents}


def queue_course(course_id, *, store=None, manual=False, configuration=None):
    """Queue only missing work; OFF still exposes pending counts without paid calls."""
    store = store or default_store()
    configuration = configuration or EmbeddingConfiguration()
    status = index_status(course_id, store=store, configuration=configuration)
    if status['remaining_chunks'] and (manual or store.load_settings()['auto_index']):
        store.queue_index_job(course_id, configuration, manual=manual)
    return index_status(course_id, store=store, configuration=configuration)


def recover_jobs(store):
    """Called only after acquiring the single worker lock; recover an interrupted batch."""
    for course_id, job in store.index_jobs():
        if job.get('status') == 'running':
            job.update(status='queued', error=None)
            store.save_index_job(course_id, job)
    if store.load_settings()['auto_index']:
        for course_id in store.saved_current_course_ids():
            snapshot = store.load_course(course_id)
            if snapshot and not snapshot.get('materials_removed') and snapshot.get('ingestion_status') != 'running':
                queue_course(course_id, store=store)


class _Paused(Exception):
    pass


def process_jobs(store, *, provider=None, batch_size=32):
    """Drain saved jobs once; keep successful batches, and never hot-loop failures."""
    reports = []
    for course_id, job in store.index_jobs():
        if job.get('status') != 'queued':
            continue
        if job.get('reason') != 'manual' and not store.load_settings()['auto_index']:
            job['status'] = 'paused'
            store.save_index_job(course_id, job)
            continue
        configuration = EmbeddingConfiguration(**job['configuration'])
        job.update(status='running', error=None, failed_hashes=[])
        store.save_index_job(course_id, job)
        def progress(report):
            job['generated'] = report['generated']
            store.save_index_job(course_id, job)
            if job.get('reason') != 'manual' and not store.load_settings()['auto_index']:
                raise _Paused()
        def failed(batch):
            job['failed_hashes'] = [item['text_hash'] for item in batch]
        try:
            report = embed_course(course_id, store=store, provider=provider, configuration=configuration,
                                  batch_size=batch_size, on_progress=progress, on_batch_error=failed)
            job.update(status='completed', last_successful_at=datetime.now(timezone.utc).isoformat(),
                       failed_hashes=[], retry_after=None, generated=report['generated'], reused=report['reused'])
            reports.append(report)
        except _Paused:
            job.update(status='paused')
        except Exception:
            # No raw provider, text or credential details are persisted/exposed.
            job.update(status='failed', error='Course search could not be updated. Please retry.',
                       retry_after=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat())
        store.save_index_job(course_id, job)
    return reports
