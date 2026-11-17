"""In-app OpenAI key: stored locally, never returned, verified for free, and usage is kept per key."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx2 as httpx
import openai

from backend.app import main, openai_key_service as oks
from backend.app.api_usage_service import UsageRecorder, usage_summary
from backend.app.embedding_service import EmbeddingConfiguration, OpenAIEmbeddingProvider
from backend.app.ingestion_store import IngestionStore
from backend.app.llm_service import OpenAIAnswerProvider
from backend.app.vision_service import OpenAIVisionExtractionProvider, VisionProviderError
from backend.tests.test_retention_api import request

KEY_A = "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAA1111"
KEY_B = "sk-proj-BBBBBBBBBBBBBBBBBBBBBBBBBBBB2222"


class FakeClient:
    def __init__(self, error=None):
        self.error, self.listed = error, 0
        self.models = self
    def list(self):
        self.listed += 1
        if self.error:
            raise self.error
        return iter([object()])
    def close(self):
        pass


def api_error(cls, status):
    response = httpx.Response(status, request=httpx.Request("GET", "https://api.openai.com/v1/models"))
    return cls("provider text that mentions the key sk-LEAK", response=response, body=None)


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("key-student", Path(self.directory.name) / "test.sqlite3")
        env = patch.dict(os.environ, {"OPENAI_API_KEY": KEY_A})
        env.start()
        self.addCleanup(env.stop)

    def spend(self, cents=1, store=None):
        """One recorded embedding call (priced) through the real recorder."""
        UsageRecorder(store or self.store).record({"usage": {"input_tokens": 1_000_000 * cents // 2, "prompt_tokens": 0}},
                                                  feature="embedding", operation="embeddings.create", model="text-embedding-3-small")


class ResolutionTests(Base):
    def test_fingerprint_is_one_way_and_hint_is_last_four(self):
        self.assertEqual(oks.hint(KEY_A), "1111")
        self.assertNotIn(KEY_A, oks.fingerprint(KEY_A))
        self.assertEqual(len(oks.fingerprint(KEY_A)), 16)
        self.assertNotEqual(oks.fingerprint(KEY_A), oks.fingerprint(KEY_B))

    def test_saved_key_beats_env_and_env_is_the_fallback(self):
        self.assertEqual(oks.resolve_api_key(self.store), (KEY_A, "environment"))
        self.store.save_api_key(KEY_B)
        self.assertEqual(oks.resolve_api_key(self.store), (KEY_B, "saved"))
        self.store.delete_api_key()
        self.assertEqual(oks.resolve_api_key(self.store), (KEY_A, "environment"))

    def test_without_a_store_only_the_environment_is_used(self):
        self.store.save_api_key(KEY_B)
        self.assertEqual(oks.resolve_api_key(None), (KEY_A, "environment"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(oks.resolve_api_key(None), (None, None))

    def test_unreadable_store_falls_back_to_env(self):
        broken = Mock()
        broken.load_api_key.side_effect = OSError("disk")
        self.assertEqual(oks.resolve_api_key(broken), (KEY_A, "environment"))

    def test_status_shows_only_source_and_last_four(self):
        self.store.save_api_key(KEY_B)
        status = oks.key_status(self.store)
        self.assertEqual(status, {"configured": True, "source": "saved", "hint": "2222"})
        self.assertNotIn("BBBB", json.dumps(status))


class ValidationTests(Base):
    def test_malformed_input_is_rejected_without_echoing_it(self):
        for bad in ("", "   ", None, 7, "short", "no-prefix-AAAAAAAAAAAAAAAAAAAAAAAA", "sk-has space AAAAAAAAAAAAAAAAAAAA", "sk-" + "A" * 500):
            with self.assertRaises(oks.KeyRejected) as caught:
                oks.validate_format(bad)
            if isinstance(bad, str) and bad.strip():
                self.assertNotIn(bad.strip(), str(caught.exception))
        self.assertEqual(oks.validate_format(f"  {KEY_A}  "), KEY_A)

    def test_verify_maps_openai_outcomes_and_never_leaks_provider_text(self):
        client = FakeClient()
        oks.verify_key(KEY_A, lambda key: client)
        self.assertEqual(client.listed, 1)
        with self.assertRaises(oks.KeyRejected) as rejected:
            oks.verify_key(KEY_A, lambda key: FakeClient(api_error(openai.AuthenticationError, 401)))
        with self.assertRaises(oks.KeyUnverifiable) as restricted:
            oks.verify_key(KEY_A, lambda key: FakeClient(api_error(openai.PermissionDeniedError, 403)))
        with self.assertRaises(oks.KeyUnverifiable) as down:
            oks.verify_key(KEY_A, lambda key: FakeClient(openai.APIConnectionError(request=httpx.Request("GET", "https://x"))))
        for caught in (rejected, restricted, down):
            self.assertNotIn("sk-", str(caught.exception))


class SavingAndUsageTests(Base):
    def test_a_rejected_key_is_not_saved(self):
        with self.assertRaises(oks.KeyRejected):
            oks.save_key(self.store, KEY_B, lambda key: FakeClient(api_error(openai.AuthenticationError, 401)))
        self.assertIsNone(self.store.load_api_key())

    def test_unverifiable_key_is_not_saved(self):
        with self.assertRaises(oks.KeyUnverifiable):
            oks.save_key(self.store, KEY_B, lambda key: FakeClient(openai.APIConnectionError(request=httpx.Request("GET", "https://x"))))
        self.assertIsNone(self.store.load_api_key())

    def test_new_key_starts_at_zero_and_the_previous_key_keeps_its_spend(self):
        self.spend(2); self.spend(2)                         # two calls on the .env key (…1111)
        before = usage_summary(self.store)
        self.assertEqual(before["total"]["calls"], 2)
        oks.save_key(self.store, KEY_B, lambda key: FakeClient())
        after = usage_summary(self.store)
        self.assertEqual((after["total"]["calls"], after["this_month"]["calls"], after["total"]["estimated_cost_usd"]), (0, 0, 0))
        self.spend(1)                                        # one call on the new key (…2222)
        now = usage_summary(self.store)
        self.assertEqual(now["total"]["calls"], 1)
        by = {item["hint"]: item for item in now["by_key"]}
        self.assertEqual(set(by), {"1111", "2222"})
        self.assertEqual((by["1111"]["total"]["calls"], by["1111"]["current"]), (2, False))
        self.assertEqual((by["2222"]["total"]["calls"], by["2222"]["current"]), (1, True))
        self.assertAlmostEqual(by["1111"]["total"]["estimated_cost_usd"], before["total"]["estimated_cost_usd"])
        self.assertEqual(now["by_key"][0]["hint"], "2222")  # key in use is listed first

    def test_switching_back_restores_that_keys_totals_without_double_counting(self):
        self.spend(2)
        oks.save_key(self.store, KEY_B, lambda key: FakeClient())
        self.spend(1)
        oks.remove_key(self.store)                           # falls back to the .env key (…1111)
        summary = usage_summary(self.store)
        self.assertEqual(summary["total"]["calls"], 1)       # only the …1111 call from before
        self.assertEqual(sum(item["total"]["calls"] for item in summary["by_key"]), 2)

    def test_untagged_history_is_given_to_the_key_active_at_the_time_of_the_first_summary(self):
        with self.store._connection() as connection:        # a pre-tracking record
            connection.execute("INSERT INTO api_usage (namespace, operation_id, recorded_at, operation, feature, model, input_tokens,"
                               " output_tokens, cached_input_tokens, estimated_cost_usd, pricing_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (self.store.namespace, "old", datetime.now(timezone.utc).isoformat(), "x", "embedding", "m", 5, 0, 0, 0.5, "v"))
        self.assertEqual(usage_summary(self.store)["total"]["calls"], 1)   # attributed to …1111, not lost
        oks.save_key(self.store, KEY_B, lambda key: FakeClient())
        summary = usage_summary(self.store)
        self.assertEqual(summary["total"]["calls"], 0)
        self.assertEqual({i["hint"]: i["total"]["calls"] for i in summary["by_key"]}["1111"], 1)

    def test_history_without_any_key_is_still_visible(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.spend(1)
            summary = usage_summary(self.store)
        self.assertEqual(summary["total"]["calls"], 1)
        self.assertIsNone(summary["by_key"][0]["hint"])

    def test_recorded_rows_hold_a_fingerprint_not_the_key(self):
        self.spend(1)
        text = json.dumps(self.store.load_api_usage())
        self.assertNotIn(KEY_A, text)
        self.assertNotIn("AAAA", text)
        self.assertIn(oks.fingerprint(KEY_A), text)


class ProvidersUseTheKeyTests(Base):
    def test_all_three_providers_use_the_saved_key_over_env(self):
        self.store.save_api_key(KEY_B)
        recorder = UsageRecorder(self.store)
        self.assertEqual(OpenAIEmbeddingProvider(EmbeddingConfiguration(), usage_recorder=recorder)._api_key, KEY_B)
        self.assertEqual(OpenAIAnswerProvider(usage_recorder=recorder)._api_key, KEY_B)
        self.assertEqual(OpenAIVisionExtractionProvider(usage_recorder=recorder)._api_key, KEY_B)

    def test_providers_fall_back_to_env_and_fail_clearly_with_no_key(self):
        recorder = UsageRecorder(self.store)
        self.assertEqual(OpenAIAnswerProvider(usage_recorder=recorder)._api_key, KEY_A)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("backend.app.llm_service.load_dotenv"), \
             patch("backend.app.embedding_service.load_dotenv"), patch("backend.app.vision_service.load_dotenv"):
            with self.assertRaises(ValueError) as caught:
                OpenAIAnswerProvider(usage_recorder=recorder)
            self.assertIn("Settings", str(caught.exception))
            with self.assertRaises(ValueError):
                OpenAIEmbeddingProvider(EmbeddingConfiguration(), usage_recorder=recorder)
            with self.assertRaises(VisionProviderError):
                OpenAIVisionExtractionProvider(usage_recorder=recorder)

    def test_a_key_saved_after_construction_is_used_by_the_next_provider_without_restart(self):
        recorder = UsageRecorder(self.store)
        self.assertEqual(OpenAIAnswerProvider(usage_recorder=recorder)._api_key, KEY_A)
        self.store.save_api_key(KEY_B)
        self.assertEqual(OpenAIAnswerProvider(usage_recorder=recorder)._api_key, KEY_B)


class ApiTests(Base):
    def setUp(self):
        super().setUp()
        patch.object(main, "default_store", return_value=self.store).start()
        self.addCleanup(patch.stopall)

    def call(self, method, path, body=None):
        return asyncio.run(request(method, path, body))

    def test_put_verifies_saves_and_returns_only_a_masked_status(self):
        with patch.object(oks, "verify_key") as verify:
            code, data = self.call("PUT", "/api/openai-key", {"api_key": KEY_B})
        self.assertEqual(code, 200)
        verify.assert_called_once()
        self.assertEqual(data, {"configured": True, "source": "saved", "hint": "2222"})
        self.assertEqual(self.store.load_api_key(), KEY_B)

    def test_the_key_never_appears_in_any_response(self):
        with patch.object(oks, "verify_key"):
            responses = [self.call("PUT", "/api/openai-key", {"api_key": KEY_B}),
                         self.call("GET", "/api/openai-key"), self.call("GET", "/api/search/settings"),
                         self.call("PATCH", "/api/search/settings", {"auto_index": True}),
                         self.call("DELETE", "/api/openai-key")]
        for code, data in responses:
            text = json.dumps(data)
            self.assertNotIn(KEY_B, text); self.assertNotIn("BBBBBBBB", text); self.assertNotIn(KEY_A, text)
        self.assertEqual(responses[2][1]["api_key"], {"configured": True, "source": "saved", "hint": "2222"})

    def test_invalid_and_unverified_keys_are_refused_without_echo_and_not_saved(self):
        secret = "sk-SECRET-but-too-short"
        code, data = self.call("PUT", "/api/openai-key", {"api_key": secret})
        self.assertEqual(code, 422); self.assertNotIn("SECRET", json.dumps(data))
        for body in ({"api_key": KEY_B, "extra": 1}, {"other": KEY_B}, [KEY_B], "x" + KEY_B):
            code, data = self.call("PUT", "/api/openai-key", body)
            self.assertEqual(code, 422); self.assertNotIn("BBBB", json.dumps(data))
        with patch.object(oks, "verify_key", side_effect=oks.KeyRejected("OpenAI rejected this key. Check it and try again.")):
            self.assertEqual(self.call("PUT", "/api/openai-key", {"api_key": KEY_B})[0], 422)
        with patch.object(oks, "verify_key", side_effect=oks.KeyUnverifiable("Could not verify")):
            self.assertEqual(self.call("PUT", "/api/openai-key", {"api_key": KEY_B})[0], 503)
        self.assertIsNone(self.store.load_api_key())

    def test_delete_returns_to_the_env_key(self):
        self.store.save_api_key(KEY_B)
        code, data = self.call("DELETE", "/api/openai-key")
        self.assertEqual((code, data["source"], data["hint"]), (200, "environment", "1111"))
        self.assertIsNone(self.store.load_api_key())

    def test_settings_usage_follows_the_active_key(self):
        self.spend(1)
        with patch.object(oks, "verify_key"):
            self.call("PUT", "/api/openai-key", {"api_key": KEY_B})
        usage = self.call("GET", "/api/search/settings")[1]["usage"]
        self.assertEqual(usage["total"]["calls"], 0)
        self.assertEqual(sorted(item["hint"] for item in usage["by_key"]), ["1111"])


if __name__ == "__main__":
    unittest.main()
