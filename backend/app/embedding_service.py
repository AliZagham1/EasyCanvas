"""Resumable incremental indexing, shared by the sync worker and explicit CLI."""

import argparse
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

from backend.app.ingestion_store import IngestionStore, validate_vector


@dataclass(frozen=True)
class EmbeddingConfiguration:
    """All settings that determine vector compatibility and cache identity."""

    provider: str = "openai"
    model: str = "text-embedding-3-small"
    dimensions: int = 1536
    version: str = "course-embeddings-v1"
    preprocessing: str = "exact-text-v1"
    storage: str = "float32-le-v1"

    def __post_init__(self):
        if not isinstance(self.dimensions, int) or isinstance(self.dimensions, bool) or self.dimensions <= 0:
            raise ValueError("Embedding dimensions must be a positive integer.")
        if any(not isinstance(value, str) or not value.strip() for key, value in asdict(self).items() if key != "dimensions"):
            raise ValueError("Embedding configuration fields must be nonempty strings.")

    @property
    def json(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @property
    def fingerprint(self):
        return hashlib.sha256(self.json.encode()).hexdigest()


class EmbeddingProviderError(Exception):
    """Safe provider failure; never contains headers, input text, or raw bodies."""


class OpenAIEmbeddingProvider:
    """Call only the OpenAI embedding endpoint, with bounded transient retries."""

    def __init__(self, configuration=None, api_key=None, usage_recorder=None):
        self.configuration = configuration or EmbeddingConfiguration()
        if self.configuration.provider != "openai":
            raise ValueError("This provider supports only OpenAI.")
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError("Set OPENAI_API_KEY in your local .env before embedding.")
        from backend.app.api_usage_service import UsageRecorder
        self.usage_recorder = usage_recorder if usage_recorder is not None else UsageRecorder()

    def embed_texts(self, texts):
        """Return vectors in input order plus reported prompt-token usage."""
        if not isinstance(texts, list) or not 1 <= len(texts) <= 32:
            raise ValueError("Embedding batches must contain 1 to 32 texts.")
        # UTF-8 byte bound conservatively stays below the 8,192-token input cap;
        # 32 inputs also stay below the API's 300,000-token aggregate cap.
        if any(not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 8000 for text in texts):
            raise ValueError("Embedding inputs must be nonblank and at most 8,000 UTF-8 bytes.")
        payload = {"model": self.configuration.model, "dimensions": self.configuration.dimensions,
                   "encoding_format": "float", "input": texts}
        for attempt in range(3):
            try:
                response = requests.post(
                    "https://api.openai.com/v1/embeddings", json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=(10, 60), allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError):
                if self.usage_recorder:
                    self.usage_recorder.record({}, feature='embedding', operation='embeddings.transport_error', model=self.configuration.model)
                if attempt == 2:
                    raise EmbeddingProviderError("Embedding provider connection failed after retries.") from None
                time.sleep(2 ** attempt)
                continue
            except requests.RequestException:
                if self.usage_recorder:
                    self.usage_recorder.record({}, feature='embedding', operation='embeddings.transport_error', model=self.configuration.model)
                raise EmbeddingProviderError("Embedding provider request failed.") from None
            try:
                if self.usage_recorder:
                    try:
                        usage_body = response.json()
                    except ValueError:
                        usage_body = {}
                    self.usage_recorder.record(usage_body, feature='embedding', operation='embeddings.create',
                                              model=self.configuration.model, request_id=response.headers.get('x-request-id'))
                if response.status_code != 200:
                    try:
                        error = response.json().get("error", {})
                        quota = isinstance(error, dict) and "insufficient_quota" in (error.get("code"), error.get("type"))
                    except (ValueError, AttributeError, TypeError):
                        quota = False
                    if quota:
                        raise EmbeddingProviderError("OpenAI API quota unavailable. Check API billing/project credits.")
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    try:
                        delay = float(response.headers.get("Retry-After", 2 ** attempt))
                    except (TypeError, ValueError):
                        delay = 2 ** attempt
                    time.sleep(max(0, min(delay, 10)))
                    continue
                if response.status_code != 200:
                    raise EmbeddingProviderError(f"Embedding provider returned HTTP {response.status_code}.")
                try:
                    body = response.json()
                    if not isinstance(body, dict) or body.get("model") != self.configuration.model:
                        raise ValueError("Model mismatch")
                    data = body["data"]
                    if not isinstance(data, list) or len(data) != len(texts):
                        raise ValueError("Wrong response count")
                    vectors = [None] * len(texts)
                    for item in data:
                        index = item["index"]
                        if type(index) is not int or not 0 <= index < len(texts) or vectors[index] is not None:
                            raise ValueError("Invalid response ordering")
                        validate_vector(item["embedding"], self.configuration.dimensions)
                        vectors[index] = item["embedding"]
                    tokens = (body.get("usage") or {}).get("prompt_tokens")
                    if tokens is not None and (type(tokens) is not int or tokens < 0):
                        raise ValueError("Invalid usage")
                except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
                    raise EmbeddingProviderError("Embedding provider returned an invalid response.") from None
                return {"vectors": vectors, "prompt_tokens": tokens}
            finally:
                response.close()


def default_store():
    from backend.app.canvas_service import get_cache_namespace
    return IngestionStore(get_cache_namespace())


def validate_course_id(course_id):
    if type(course_id) is not int or course_id <= 0:
        raise ValueError("course_id must be a positive integer.")


def embed_course(course_id, *, store=None, provider=None, configuration=None, batch_size=32, on_progress=None, document_keys=None, on_batch_error=None):
    """Index only missing current eligible inputs; successful batches survive failure."""
    validate_course_id(course_id)
    if type(batch_size) is not int or not 1 <= batch_size <= 32:
        raise ValueError("batch_size must be between 1 and 32.")
    store = store or default_store()
    configuration = configuration or (provider.configuration if provider else EmbeddingConfiguration())
    if provider and provider.configuration != configuration:
        raise ValueError("Provider configuration does not match indexing configuration.")
    if store.load_course(course_id) is None:
        raise ValueError("No saved course snapshot for this account/course.")
    started = time.monotonic()
    inputs = store.load_embedding_inputs(course_id)
    if document_keys is not None:
        if (not isinstance(document_keys, (list, tuple, set))
                or any(not isinstance(key, str) or not key for key in document_keys)):
            raise ValueError('document_keys must be a collection of nonempty document keys.')
        selected = set(document_keys)
        inputs = [item for item in inputs if any(origin['document_key'] in selected for origin in item['sources'])]
    cached = store.load_embeddings(course_id, configuration)
    missing = [item for item in inputs if item["text_hash"] not in cached]
    report = {"course_id": course_id, "unique_inputs": len(inputs), "generated": 0,
              "reused": len(inputs) - len(missing), "prompt_tokens": 0, "usage_complete": True}
    if missing:
        from backend.app.api_usage_service import UsageRecorder
        provider = provider or OpenAIEmbeddingProvider(configuration, usage_recorder=UsageRecorder(store))
    for offset in range(0, len(missing), batch_size):
        batch = missing[offset:offset + batch_size]
        try:
            result = provider.embed_texts([item["text"] for item in batch])
            if len(result["vectors"]) != len(batch):
                raise ValueError("Embedding provider returned the wrong vector count.")
            store.save_embeddings(course_id, configuration, [
                {"text_hash": item["text_hash"], "vector": vector}
                for item, vector in zip(batch, result["vectors"])
            ])
        except Exception:
            if on_batch_error:
                on_batch_error(batch)
            raise
        report["generated"] += len(batch)
        if result.get("prompt_tokens") is None:
            report["usage_complete"] = False
        else:
            report["prompt_tokens"] += result["prompt_tokens"]
        if on_progress:
            on_progress(dict(report))
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def embed_saved_current_courses(*, store=None, provider=None, configuration=None, on_progress=None):
    """Explicitly index the saved sync's course list without requesting Canvas."""
    store = store or default_store()
    courses = store.saved_current_course_ids()
    if not courses:
        raise ValueError("No saved current-course list. Run course ingestion first.")
    reports = []
    for course_id in courses:
        reports.append(embed_course(course_id, store=store, provider=provider,
                                    configuration=configuration, on_progress=on_progress))
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explicitly embed saved course chunks; this uses OpenAI API credits.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--course", type=int)
    selection.add_argument("--all-current", action="store_true")
    args = parser.parse_args()
    try:
        def progress(report):
            print(json.dumps(report), flush=True)
        result = (embed_saved_current_courses(on_progress=progress) if args.all_current
                  else embed_course(args.course, on_progress=progress))
        print(json.dumps(result, indent=2))
    except (ValueError, EmbeddingProviderError) as error:
        parser.exit(1, f"Indexing stopped: {error}\nSuccessful earlier batches remain cached.\n")
