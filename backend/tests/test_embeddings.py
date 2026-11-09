import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.app.chunking_service import ensure_course_chunks
from backend.app import chunking_service
from backend.app.embedding_service import (
    EmbeddingConfiguration, EmbeddingProviderError, OpenAIEmbeddingProvider,
    embed_course, embed_saved_current_courses,
)
from backend.app.ingestion_store import IngestionStore, encode_vector, decode_vector, validate_vector
from backend.app.retrieval_service import cosine_similarity, search_course


class FakeProvider:
    def __init__(self, configuration=None, fail_on=None):
        self.configuration = configuration or EmbeddingConfiguration(dimensions=3)
        self.calls = []
        self.fail_on = fail_on

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        if len(self.calls) == self.fail_on:
            raise EmbeddingProviderError("Fake temporary failure")
        vectors = []
        for text in texts:
            if "process" in text.lower():
                vectors.append([1, 0, 0])
            elif "matrix" in text.lower():
                vectors.append([0, 1, 0])
            else:
                vectors.append([0, 0, 1])
        return {"vectors": vectors, "prompt_tokens": len(texts)}


class VectorTests(unittest.TestCase):
    def test_float32_round_trip_and_byte_size(self):
        vector = [0.1, -0.25, 0.333333333]
        packed = encode_vector(vector, 3)
        self.assertEqual(len(packed), 12)
        for actual, expected in zip(decode_vector(packed, 3), vector):
            self.assertAlmostEqual(actual, expected, places=7)

    def test_invalid_vectors_are_rejected(self):
        for vector in ([0, 0, 0], [math.nan, 1, 0], [math.inf, 1, 0], [True, 1, 0], ["1", 0, 1], [1, 2], None, [1.7e308]*3):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                validate_vector(vector, 3)
        with self.assertRaises(ValueError):
            decode_vector(b"short", 3)
        for vector in ([1e100, 0, 0], [1e-100, 0, 0]):
            with self.assertRaises(ValueError):
                encode_vector(vector, 3)

    def test_cosine_known_cases_and_invalid_comparisons(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [2, 0]), 1)
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 1]), 0)
        self.assertAlmostEqual(cosine_similarity([1, 0], [-1, 0]), -1)
        for right in ([0, 0], [1], [math.inf, 0]):
            with self.assertRaises(ValueError):
                cosine_similarity([1, 0], right)

    def test_configuration_fingerprint_is_stable_and_sensitive(self):
        config = EmbeddingConfiguration(dimensions=3)
        self.assertEqual(config.fingerprint, EmbeddingConfiguration(dimensions=3).fingerprint)
        for changed in (replace(config, dimensions=2), replace(config, model="other-model"), replace(config, version="v2")):
            self.assertNotEqual(config.fingerprint, changed.fingerprint)
        for size in (0, -1, True):
            with self.assertRaises(ValueError):
                EmbeddingConfiguration(dimensions=size)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.config = EmbeddingConfiguration(dimensions=3)
        self.provider = OpenAIEmbeddingProvider(self.config, api_key="test-only-not-a-real-key", usage_recorder=False)

    def response(self, body=None, status=200):
        response = Mock(status_code=status, headers={})
        response.json.return_value = body or {
            "model": self.config.model, "data": [{"index": 0, "embedding": [1, 0, 0]}],
            "usage": {"prompt_tokens": 4},
        }
        return response

    def test_response_ordering_and_request_settings(self):
        response = self.response({"model": self.config.model, "data": [
            {"index": 1, "embedding": [0, 1, 0]}, {"index": 0, "embedding": [1, 0, 0]}],
            "usage": {"prompt_tokens": 8}})
        with patch("backend.app.embedding_service.requests.post", return_value=response) as post:
            result = self.provider.embed_texts(["one", "two"])
        self.assertEqual(result["vectors"], [[1, 0, 0], [0, 1, 0]])
        self.assertEqual(result["prompt_tokens"], 8)
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_args.kwargs["json"]["dimensions"], 3)
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/embeddings")
        response.close.assert_called_once()

    def test_bad_response_shapes_indices_models_and_values(self):
        bodies = [[], {"model": "wrong", "data": []}, {"model": self.config.model, "data": []}]
        for index in (-1, 1, True):
            bodies.append({"model": self.config.model, "data": [{"index": index, "embedding": [1, 0, 0]}]})
        for vector in ([0, 0, 0], [1, 2], [math.nan, 1, 0], [True, 0, 1]):
            bodies.append({"model": self.config.model, "data": [{"index": 0, "embedding": vector}]})
        for body in bodies:
            response = self.response(); response.json.return_value = body
            with patch("backend.app.embedding_service.requests.post", return_value=response), self.assertRaises(EmbeddingProviderError):
                self.provider.embed_texts(["one"])

    def test_duplicate_indices_are_rejected(self):
        response = self.response({"model": self.config.model, "data": [
            {"index": 0, "embedding": [1, 0, 0]}, {"index": 0, "embedding": [0, 1, 0]}]})
        with patch("backend.app.embedding_service.requests.post", return_value=response), self.assertRaises(EmbeddingProviderError):
            self.provider.embed_texts(["one", "two"])

    def test_rate_limit_and_timeout_retries_are_bounded(self):
        with patch("backend.app.embedding_service.requests.post", side_effect=[self.response(status=429), requests.Timeout(), self.response()]) as post, patch("backend.app.embedding_service.time.sleep"):
            self.assertEqual(self.provider.embed_texts(["one"])["prompt_tokens"], 4)
        self.assertEqual(post.call_count, 3)
        with patch("backend.app.embedding_service.requests.post", side_effect=requests.Timeout("secret request details")) as post, patch("backend.app.embedding_service.time.sleep"), self.assertRaises(EmbeddingProviderError) as caught:
            self.provider.embed_texts(["one"])
        self.assertEqual(post.call_count, 3)
        self.assertNotIn("secret", str(caught.exception))

    def test_auth_failure_is_not_retried_or_exposed(self):
        response = self.response(status=401)
        response.text = "private response body"
        with patch("backend.app.embedding_service.requests.post", return_value=response) as post, self.assertRaises(EmbeddingProviderError) as caught:
            self.provider.embed_texts(["one"])
        self.assertEqual(post.call_count, 1)
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn("test-only", str(caught.exception))

    def test_insufficient_quota_is_not_retried_or_exposed(self):
        for field in ("type", "code"):
            response = self.response({"error": {field: "insufficient_quota", "message": "private account details"}}, status=429)
            with patch("backend.app.embedding_service.requests.post", return_value=response) as post, self.assertRaises(EmbeddingProviderError) as caught:
                self.provider.embed_texts(["one"])
            self.assertEqual(post.call_count, 1)
            self.assertIn("billing", str(caught.exception))
            self.assertNotIn("private", str(caught.exception))

    def test_invalid_inputs_do_not_request_provider(self):
        with patch("backend.app.embedding_service.requests.post") as post:
            for inputs in ([], [""], ["界" * 3000], ["a"] * 33, "text"):
                with self.assertRaises(ValueError):
                    self.provider.embed_texts(inputs)
        post.assert_not_called()

    def test_missing_usage_is_explicit_and_bad_json_is_safe(self):
        response = self.response(); response.json.return_value.pop("usage")
        with patch("backend.app.embedding_service.requests.post", return_value=response):
            self.assertIsNone(self.provider.embed_texts(["one"])["prompt_tokens"])
        response.json.side_effect = ValueError("private data")
        with patch("backend.app.embedding_service.requests.post", return_value=response), self.assertRaises(EmbeddingProviderError):
            self.provider.embed_texts(["one"])


class EmbeddingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("student-a", Path(self.directory.name) / "course.sqlite3")
        self.snapshot = {"course_id": 42, "synced_at": "first"}
        self.store.save_course(self.snapshot)
        self.provider = FakeProvider()
        self.doc = {"course_id": 42, "document_key": "file:1", "canvas_id": 1,
                    "title": "Lecture.pdf", "source_type": "file", "status": "ok",
                    "url": "https://canvas.example/courses/42/files/1", "version": "v1",
                    "text": "Processes and scheduling", "pages": []}
        self.save(self.doc)

    def save(self, document, store=None):
        store = store or self.store
        store.save_document(document["course_id"], document, "first")
        ensure_course_chunks(document["course_id"], store)

    def index(self, **kwargs):
        return embed_course(42, store=self.store, provider=self.provider, **kwargs)

    def search(self, question="Explain processes", **kwargs):
        return search_course(42, question, store=self.store, provider=self.provider, **kwargs)

    def test_eligible_duplicate_inputs_are_embedded_once_and_keep_origins(self):
        self.save({**self.doc, "document_key": "file:2", "canvas_id": 2, "title": "Second upload.pdf"})
        self.save({**self.doc, "document_key": "file:3", "text": "25", "pages": [{"page_number": 26, "text": "25", "status": "ok"}]})
        report = self.index()
        self.assertEqual(report["generated"], 1)
        self.assertEqual(self.provider.calls, [[self.doc["text"]]])
        result = self.search()["results"][0]
        self.assertEqual({s["canvas_id"] for s in result["sources"]}, {1, 2})
        self.assertTrue(all(s["locations"] for s in result["sources"]))

    def test_cache_survives_restart_and_model_configuration_changes_are_separate(self):
        self.index()
        reopened = IngestionStore("student-a", self.store.path)
        self.provider.calls.clear()
        report = embed_course(42, store=reopened, provider=self.provider)
        self.assertEqual((report["generated"], report["reused"]), (0, 1))
        self.assertEqual(self.provider.calls, [])
        for config in (replace(self.provider.configuration, model="other-model"), replace(self.provider.configuration, version="v2")):
            self.assertEqual(embed_course(42, store=reopened, provider=FakeProvider(config))["generated"], 1)
        self.assertEqual(self.index()["reused"], 1)
        with self.store._connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM chunk_embeddings').fetchone()[0], 3)

    def test_metadata_only_changes_reuse_embeddings_and_return_fresh_sources(self):
        self.index()
        self.doc.update(title="Renamed", references=[{"module_id": 7}], warnings=["example_warning"])
        self.save(self.doc)
        self.assertEqual(self.index()["generated"], 0)
        result = self.search()["results"][0]["sources"][0]
        self.assertEqual(result["title"], "Renamed")
        self.assertEqual(result["references"], [{"module_id": 7}])
        self.assertEqual(result["warnings"], ["example_warning"])

    def test_changed_text_gets_new_vector_and_old_vector_is_not_searched(self):
        self.index()
        self.doc.update(text="Matrix reduction")
        self.save(self.doc)
        self.assertEqual(self.search()["status"], "not_indexed")
        self.assertEqual(self.index()["generated"], 1)
        self.assertEqual(self.search("Explain matrix")["results"][0]["text"], "Matrix reduction")

    def test_account_and_course_isolation(self):
        self.index()
        other = IngestionStore("student-b", self.store.path)
        other.save_course(self.snapshot)
        self.save(self.doc, other)
        self.assertEqual(search_course(42, "process", store=other, provider=self.provider)["status"], "not_indexed")
        self.assertEqual(embed_course(42, store=other, provider=self.provider)["generated"], 1)
        self.store.save_course({"course_id": 43, "synced_at": "first"})
        self.save({**self.doc, "course_id": 43})
        self.assertEqual(search_course(43, "process", store=self.store, provider=self.provider)["status"], "not_indexed")

    def test_stale_removed_and_newly_ineligible_sources_are_not_retrieved(self):
        self.index()
        self.save({**self.doc, "status": "unavailable", "version": "v2", "text": ""})
        self.assertEqual(self.search()["status"], "no_content")
        self.save(self.doc)
        self.store.save_course({**self.snapshot, "synced_at": "second"})
        self.assertEqual(self.search()["status"], "no_content")
        self.store.save_course(self.snapshot)
        original = chunking_service.chunk_metadata
        with patch.object(chunking_service, "chunk_metadata", side_effect=lambda doc, chunk: {**original(doc, chunk), "embedding_eligible": False}):
            self.assertEqual(self.search()["status"], "no_content")

    def test_template_origins_are_filtered_even_in_a_duplicate_group(self):
        text = "Verify your syllabus uses the current syllabus template."
        self.save({**self.doc, "text": text})
        self.save({**self.doc, "text": text, "document_key": "syllabus:42", "source_type": "syllabus"})
        self.index()
        sources = self.search()["results"][0]["sources"]
        self.assertEqual([s["document_key"] for s in sources], ["file:1"])
        self.save({**self.doc, "text": text, "source_type": "syllabus"})
        before = len(self.provider.calls)
        self.assertEqual(self.search()["status"], "no_content")
        self.assertEqual(len(self.provider.calls), before)

    def test_cosine_ranking_and_partial_index_never_index_during_search(self):
        self.index()
        self.save({**self.doc, "document_key": "file:2", "text": "Matrix reduction"})
        result = self.search()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["missing_inputs"], 1)
        self.assertEqual(self.provider.calls[-1], ["Explain processes"])
        self.index()
        result = self.search()
        self.assertEqual(result["status"], "ready")
        self.assertEqual([r["score"] for r in result["results"]], [1, 0])

    def test_missing_index_does_not_call_provider(self):
        self.assertEqual(self.search()["status"], "not_indexed")
        self.assertEqual(self.provider.calls, [])

    def test_failed_batch_resume_keeps_prior_successes(self):
        self.save({**self.doc, "document_key": "file:2", "text": "Matrix reduction"})
        self.save({**self.doc, "document_key": "file:3", "text": "Other material"})
        with self.assertRaises(EmbeddingProviderError):
            embed_course(42, store=self.store, provider=FakeProvider(fail_on=2), batch_size=1)
        result = self.index(batch_size=1)
        self.assertEqual((result["generated"], result["reused"]), (2, 1))

    def test_invalid_batch_is_not_partially_saved(self):
        fake = FakeProvider()
        fake.embed_texts = lambda texts: {"vectors": [[0, 0, 0]], "prompt_tokens": 1}
        with self.assertRaises(ValueError):
            embed_course(42, store=self.store, provider=fake)
        self.assertEqual(self.store.load_embeddings(42, fake.configuration), {})

    def test_saved_current_courses_use_saved_list_without_canvas(self):
        self.store.save_sync_state({"courses": [{"course_id": 42}, {"course_id": 42}]})
        with patch("requests.sessions.Session.request", side_effect=AssertionError("No real API")):
            reports = embed_saved_current_courses(store=self.store, provider=self.provider)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["generated"], 1)

    def test_invalid_inputs_and_configuration_mismatch(self):
        for course_id in (True, -1, "42"):
            with self.assertRaises(ValueError):
                embed_course(course_id, store=self.store, provider=self.provider)
        for question in ("", " ", "界"*3000):
            with self.assertRaises(ValueError):
                self.search(question)
        with self.assertRaises(ValueError):
            self.search(top_k=0)
        with self.assertRaises(ValueError):
            self.index(configuration=EmbeddingConfiguration(dimensions=2))
        with self.assertRaises(ValueError):
            embed_course(999, store=self.store, provider=self.provider)

    def test_question_provider_errors_do_not_change_index(self):
        self.index()
        with self.assertRaises(EmbeddingProviderError):
            search_course(42, "process", store=self.store, provider=FakeProvider(fail_on=1))
        self.assertEqual(self.index()["reused"], 1)


if __name__ == "__main__":
    unittest.main()
