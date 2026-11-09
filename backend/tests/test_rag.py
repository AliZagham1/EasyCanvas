import copy
import json
import unittest
from unittest.mock import Mock, patch, AsyncMock
from backend.app.query_understanding_service import empty_understanding

import requests

from backend.app.rag_service import answer_question, RAGValidationError, RAG_INSTRUCTIONS
from backend.app.llm_service import OpenAIAnswerProvider, LLMProviderError, LLMRefusalError


def retrieval_result(status="ready", count=1):
    return {"status": status, "results": [
        {"text_hash": f"hash-{number}", "score": .7, "text": "FCFS is simple, but short jobs can wait behind long jobs.",
         "sources": [{"course_id": 42, "document_key": f"file:{number}", "canvas_id": number,
                      "chunk_index": 0, "title": "Lecture.pptx", "source_type": "file", "url": "https://canvas.example/source",
                      "locations": [{"kind": "slide", "slide_number": 6}], "status": "ok", "warnings": [],
                      "embedding_eligible": True, "source_quality": {"policy_retrieval_eligible": True, "flags": []}}]}
        for number in range(count)]}


class FakeLLM:
    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer if answer is not None else {
            "status": "answered", "blocks": [{"text": "FCFS is simple, but short jobs may wait behind long jobs.", "source_ids": ["S1"]}],
            "limitations": [],
        }

    def generate(self, *args):
        self.calls.append(args)
        return {"answer": copy.deepcopy(self.answer), "model": "fake-answer-model", "usage": {"input_tokens": 5, "output_tokens": 5}}

    async def rewrite_query(self, cid, question, context):
        return empty_understanding(question, use_active_documents=bool(context.get("active_documents")))


class RAGTests(unittest.TestCase):
    def setUp(self):
        self.retrieval = retrieval_result()
        self.search = patch("backend.app.rag_service.retrieval_service.retrieve_evidence", side_effect=lambda *args, **kwargs: copy.deepcopy(self.retrieval)).start()
        self.addCleanup(patch.stopall)
        self.store = Mock()
        self.store.load_course.return_value = {"course_id": 42}
        patch("backend.app.rag_service.default_store", return_value=self.store).start()
        patch("backend.app.rag_service.query_understanding_service.understand_question",
              new=AsyncMock(return_value=empty_understanding("Explain FCFS"))).start()
        self.provider = FakeLLM()

    def ask(self, **kwargs):
        return answer_question(42, "Explain FCFS", llm_provider=self.provider, **kwargs)

    def test_valid_answer_has_backend_citations_and_original_metadata(self):
        result = self.ask()
        self.assertEqual(result["status"], "answered")
        self.assertTrue(result["answer"].endswith("[S1]"))
        origin = result["sources"][0]["origins"][0]
        self.assertEqual(origin["locations"][0]["slide_number"], 6)
        self.assertEqual(origin["url"], "https://canvas.example/source")
        self.assertEqual(self.search.call_args.args, (42, "Explain FCFS"))
        self.assertEqual(self.search.call_args.kwargs, {"store": self.store, "provider": None})

    def test_context_is_bounded_to_five_passages(self):
        self.retrieval = retrieval_result(count=8)
        self.ask()
        context = self.provider.calls[0][3]
        self.assertEqual([p["id"] for p in context], ["S1", "S2", "S3", "S4", "S5"])

    def test_duplicate_passages_keep_all_sources_but_are_sent_once(self):
        duplicate = copy.deepcopy(self.retrieval["results"][0])
        duplicate["sources"][0]["document_key"] = "file:other"
        self.retrieval["results"].append(duplicate)
        result = self.ask()
        self.assertEqual(len(self.provider.calls[0][3]), 1)
        self.assertEqual(len(result["sources"][0]["origins"]), 2)

    def test_only_cited_sources_are_in_answer_sources(self):
        self.retrieval = retrieval_result(count=3)
        result = self.ask()
        self.assertEqual(len(result["retrieved_sources"]), 3)
        self.assertEqual([s["id"] for s in result["sources"]], ["S1"])

    def test_missing_index_or_empty_context_never_calls_answer_model(self):
        for status in ("not_indexed", "no_content"):
            self.retrieval = retrieval_result(status=status, count=0)
            result = self.ask()
            self.assertEqual(result["status"], "indexing_needed" if status == "not_indexed" else "insufficient_context")
        self.retrieval = retrieval_result(count=0)
        self.assertEqual(self.ask()["status"], "insufficient_context")
        self.assertEqual(self.provider.calls, [])

    def test_course_eligibility_and_template_flags_are_defended_before_generation(self):
        for changes in ({"course_id": 43}, {"embedding_eligible": False},
                        {"source_quality": {"policy_retrieval_eligible": False}}):
            self.retrieval = retrieval_result()
            self.retrieval["results"][0]["sources"][0].update(changes)
            self.assertEqual(self.ask()["status"], "insufficient_context")
        self.assertEqual(self.provider.calls, [])

    def test_partial_index_and_extraction_warnings_survive(self):
        self.retrieval["status"] = "partial"
        origin = self.retrieval["results"][0]["sources"][0]
        origin.update(status="partial", warnings=["legacy_doc_structure_unverified"])
        origin["locations"][0]["warnings"] = ["images_not_extracted"]
        result = self.ask()
        self.assertIn("images_not_extracted", result["warnings"])
        self.assertIn("legacy_doc_structure_unverified", result["warnings"])
        self.assertTrue(any("incomplete" in warning for warning in result["warnings"]))
        self.assertIn("images_not_extracted", self.provider.calls[0][3][0]["limitations"])

    def test_insufficient_evidence_is_not_presented_as_an_answer(self):
        self.provider.answer = {"status": "insufficient_context", "blocks": [], "limitations": ["The passages do not describe this policy."]}
        result = self.ask()
        self.assertEqual(result["status"], "insufficient_context")
        self.assertEqual(result["sources"], [])
        self.assertIn("insufficient", result["answer"])
        self.assertTrue(result["retrieved_sources"])

    def test_structured_facts_are_explicitly_deferred(self):
        self.provider.answer = {"status": "needs_structured_data", "blocks": [], "limitations": ["The document says due October 30."]}
        result = self.ask()
        self.assertEqual(result["status"], "needs_structured_data")
        self.assertNotIn("October 30", json.dumps(result))
        instructions = self.provider.calls[0][0]
        for phrase in ("due dates", "points", "submission status", "untrusted data", "general knowledge", "insufficient_context"):
            self.assertIn(phrase, instructions)

    def test_unknown_missing_and_inline_citations_are_rejected(self):
        for block in ({"text": "Unsupported", "source_ids": ["S99"]},
                      {"text": "Uncited", "source_ids": []},
                      {"text": "Claim [S99]", "source_ids": ["S1"]},
                      {"text": "Claim [S1, S99]", "source_ids": ["S1"]},
                      {"text": "Claim https://invented.example", "source_ids": ["S1"]}):
            self.provider.answer = {"status": "answered", "blocks": [block], "limitations": []}
            with self.assertRaises(RAGValidationError):
                self.ask()

    def test_valid_redundant_inline_citations_are_rendered_once(self):
        self.retrieval = retrieval_result(count=2)
        self.provider.answer = {"status": "answered", "blocks": [
            {"text": "Supported explanation. [S1, S2]", "source_ids": ["S1", "S2"]}], "limitations": []}
        result = self.ask()
        self.assertEqual(result["answer"], "Supported explanation. [S1] [S2]")
        self.assertEqual([s["id"] for s in result["sources"]], ["S1", "S2"])

    def test_valid_but_undeclared_inline_source_is_rejected(self):
        self.retrieval = retrieval_result(count=2)
        self.provider.answer = {"status": "answered", "blocks": [
            {"text": "Explanation [S1, S2]", "source_ids": ["S1"]}], "limitations": []}
        with self.assertRaises(RAGValidationError):
            self.ask()

    def test_malformed_or_contradictory_answer_shapes_are_rejected(self):
        for answer in (None, [], {}, {"status": "wrong", "blocks": [], "limitations": []},
                       {"status": "answered", "blocks": [], "limitations": []},
                       {"status": "answered", "blocks": [{"text": "", "source_ids": ["S1"]}], "limitations": []}):
            self.provider.answer = answer
            with self.assertRaises(RAGValidationError):
                self.ask()

    def test_insufficient_context_discards_contradictory_blocks_without_stream_failure(self):
        self.provider.answer = {"status": "insufficient_context", "blocks": [
            {"text": "Unsupported guess with https://invented.example", "source_ids": ["S99"]}],
            "limitations": ["The question is not readable in the provided material."]}
        result = self.ask()
        self.assertEqual(result["status"], "insufficient_context")
        self.assertEqual(result["answer_blocks"], [])
        self.assertEqual(result["sources"], [])
        self.assertNotIn("invented", json.dumps(result))

    def test_provider_failure_and_refusal_are_distinct_from_insufficient_context(self):
        provider = Mock()
        provider.generate.side_effect = LLMProviderError("Safe provider failure")
        with self.assertRaises(LLMProviderError):
            answer_question(42, "Question", llm_provider=provider)
        provider.generate.side_effect = LLMRefusalError("Safe refusal")
        self.assertEqual(answer_question(42, "Question", llm_provider=provider)["status"], "refused")

    def test_requests_are_stateless_and_do_not_mutate_retrieval(self):
        original = copy.deepcopy(self.retrieval)
        self.ask(); self.ask()
        self.assertEqual(self.retrieval, original)
        self.assertEqual(self.provider.calls[0], self.provider.calls[1])

    def test_invalid_inputs_stop_before_retrieval(self):
        for course, question in ((True, "Q"), (0, "Q"), (42, ""), (42, "界"*3000)):
            with self.assertRaises(ValueError):
                answer_question(course, question, llm_provider=self.provider)
        self.search.assert_not_called()


class LLMProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = OpenAIAnswerProvider(api_key="fake-test-key", usage_recorder=False, model="gpt-6-luna")
        self.passages = [{"id": "S1", "text": "Course evidence"}]

    def response(self, body=None, status=200):
        response = Mock(status_code=status, headers={})
        response.json.return_value = body if body is not None else {
            "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps({"status": "answered", "blocks": [{"text": "Explanation", "source_ids": ["S1"]}], "limitations": []})}]}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
        return response

    def generate(self):
        return self.provider.generate(RAG_INSTRUCTIONS, 42, "Question", self.passages)

    def test_stateless_request_schema_and_response_parsing(self):
        response = self.response()
        with patch("backend.app.llm_service.requests.post", return_value=response) as post:
            result = self.generate()
        payload = post.call_args.kwargs["json"]
        self.assertFalse(payload["store"])
        self.assertNotIn("previous_response_id", payload)
        self.assertNotIn("tools", payload)
        self.assertNotIn("fake-test-key", json.dumps(payload))
        self.assertEqual(payload["text"]["format"]["schema"]["properties"]["blocks"]["items"]["properties"]["source_ids"]["items"]["enum"], ["S1"])
        self.assertEqual(result["usage"]["output_tokens"], 20)
        response.close.assert_called_once()

    def test_model_can_be_configured(self):
        with patch.dict("os.environ", {"EASYCANVAS_ANSWER_MODEL": "configured-model"}):
            self.assertEqual(OpenAIAnswerProvider(api_key="fake-key", usage_recorder=False).model, "configured-model")
        with self.assertRaises(ValueError):
            OpenAIAnswerProvider(model="", api_key="fake-key", usage_recorder=False)

    def test_timeout_and_transient_status_retries(self):
        with patch("backend.app.llm_service.requests.post", side_effect=[requests.Timeout(), self.response(status=503), self.response()]) as post, patch("backend.app.llm_service.time.sleep"):
            self.generate()
        self.assertEqual(post.call_count, 3)

    def test_auth_and_quota_fail_without_retry_or_raw_details(self):
        for status, body in ((401, {"error": {"message": "private details"}}), (429, {"error": {"type": "insufficient_quota", "message": "fake-test-key"}})):
            with patch("backend.app.llm_service.requests.post", return_value=self.response(body, status)) as post, self.assertRaises(LLMProviderError) as caught:
                self.generate()
            self.assertEqual(post.call_count, 1)
            self.assertNotIn("private", str(caught.exception))
            self.assertNotIn("fake-test-key", str(caught.exception))

    def test_incomplete_refusal_and_bad_json_are_not_answers(self):
        bodies = [{"status": "incomplete", "output": []},
                  {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "Private text"}]}]},
                  {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "not JSON"}]}]},
                  {"status": "completed", "output": [{"type": "function_call"}]}]
        for body in bodies:
            with patch("backend.app.llm_service.requests.post", return_value=self.response(body)), self.assertRaises(LLMProviderError):
                self.generate()


if __name__ == "__main__":
    unittest.main()
