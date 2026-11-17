"""Vision on/off in Settings: a saved choice beats the .env default, and nothing here calls OpenAI."""
import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import main, visual_document_service as vds
from backend.app.ingestion_store import IngestionStore
from backend.tests.test_retention_api import request
from backend.tests.test_visual_documents import FakeVision, visual_pdf

PDF = {"filename": "Lecture.pdf", "display_name": "Lecture.pdf"}


class VisionResolutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("vision-student", Path(self.directory.name) / "test.sqlite3")

    def env(self, value):
        return patch.dict(os.environ, {"EASYCANVAS_VISION_ENABLED": value})

    def test_without_a_store_only_the_environment_is_used(self):
        with self.env("true"):
            self.assertTrue(vds.vision_enabled())
        with self.env("false"):
            self.assertFalse(vds.vision_enabled())

    def test_unset_setting_follows_the_environment_default(self):
        self.assertIsNone(self.store.load_settings()["vision_enabled"])
        with self.env("true"):
            self.assertTrue(vds.vision_enabled(self.store))
        with self.env("false"):
            self.assertFalse(vds.vision_enabled(self.store))

    def test_saved_setting_overrides_the_environment_both_ways(self):
        self.store.update_settings({"vision_enabled": False})
        with self.env("true"):
            self.assertFalse(vds.vision_enabled(self.store))
        self.store.update_settings({"vision_enabled": True})
        with self.env("false"):
            self.assertTrue(vds.vision_enabled(self.store))

    def test_unreadable_settings_fall_back_to_the_environment(self):
        broken = Mock()
        broken.load_settings.side_effect = sqlite3.OperationalError("locked")
        with self.env("true"):
            self.assertTrue(vds.vision_enabled(broken))
        with self.env("false"):
            self.assertFalse(vds.vision_enabled(broken))

    def test_pipeline_version_tracks_the_saved_setting(self):
        with self.env("false"):
            self.store.update_settings({"vision_enabled": True})
            self.assertIn(":enabled:", vds.file_pipeline_version(PDF, store=self.store))
        with self.env("true"):
            self.store.update_settings({"vision_enabled": False})
            self.assertIn(":inspection-only:", vds.file_pipeline_version(PDF, store=self.store))

    def test_toggling_alone_does_not_invalidate_complete_extraction(self):
        self.store.update_settings({"vision_enabled": False})
        off = vds.file_pipeline_version(PDF, store=self.store)
        self.store.update_settings({"vision_enabled": True})
        on = vds.file_pipeline_version(PDF, store=self.store)
        self.assertNotEqual(off, on)
        self.assertTrue(vds.same_pipeline_except_enabled(off, on))

    def test_other_settings_survive_a_vision_change(self):
        self.store.update_settings({"auto_index": False, "monthly_warning_usd": 5})
        self.store.update_settings({"vision_enabled": True})
        settings = self.store.load_settings()
        self.assertEqual((settings["auto_index"], settings["monthly_warning_usd"], settings["vision_enabled"]), (False, 5, True))


class VisionGatesTheProviderTests(unittest.TestCase):
    """The Settings choice, not just the .env default, decides whether an OpenAI vision call is made."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("vision-gate", Path(self.directory.name) / "test.sqlite3")
        self.store.save_course({"course_id": 42, "synced_at": "now"})
        self.content = visual_pdf(["Question 1"])
        self.created = []
        self.fake = FakeVision()
        def factory(*args, **kwargs):
            self.created.append(1)
            return self.fake
        patch.object(vds, "OpenAIVisionExtractionProvider", side_effect=factory).start()
        self.addCleanup(patch.stopall)

    def extract(self):
        return vds.extract_file({"filename": "Homework.pdf"}, self.content, store=self.store, course_id=42)

    def test_saved_off_makes_no_provider_and_no_call_even_if_env_says_on(self):
        self.store.update_settings({"vision_enabled": False})
        with patch.dict(os.environ, {"EASYCANVAS_VISION_ENABLED": "true"}):
            result = self.extract()
        self.assertEqual(self.created, [])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(result["pages"][0]["extraction_status"], "vision_pending")  # native text kept, visuals marked pending
        self.assertIn("Question 1", result["text"])

    def test_saved_on_calls_the_provider_even_if_env_says_off(self):
        self.store.update_settings({"vision_enabled": True})
        with patch.dict(os.environ, {"EASYCANVAS_VISION_ENABLED": "false"}):
            result = self.extract()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(result["pages"][0]["extraction_method"], "vision")

    def test_unset_follows_env_both_ways(self):
        with patch.dict(os.environ, {"EASYCANVAS_VISION_ENABLED": "false"}):
            self.extract()
        self.assertEqual(len(self.fake.calls), 0)
        with patch.dict(os.environ, {"EASYCANVAS_VISION_ENABLED": "true"}):
            self.extract()
        self.assertEqual(len(self.fake.calls), 1)

    def test_a_second_extraction_after_turning_vision_off_makes_no_new_call(self):
        self.store.update_settings({"vision_enabled": True})
        self.extract()
        calls = len(self.fake.calls)
        self.store.update_settings({"vision_enabled": False})
        self.extract()
        self.assertEqual(len(self.fake.calls), calls)


class VisionSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IngestionStore("vision-api", Path(self.directory.name) / "test.sqlite3")
        patch.object(main, "default_store", return_value=self.store).start()
        patch.object(main.app.state, "course_sync", Mock(), create=True).start()
        self.addCleanup(patch.stopall)

    def test_get_reports_effective_value_and_its_source(self):
        with patch.dict(os.environ, {"EASYCANVAS_VISION_ENABLED": "true"}):
            _, data = asyncio.run(request("GET", "/api/search/settings"))
        self.assertTrue(data["settings"]["vision_enabled"])
        self.assertTrue(data["settings"]["vision_from_environment"])

    def test_patch_saves_a_choice_that_beats_the_environment(self):
        with patch.dict(os.environ, {"EASYCANVAS_VISION_ENABLED": "true"}):
            code, data = asyncio.run(request("PATCH", "/api/search/settings", {"vision_enabled": False}))
            self.assertEqual(code, 200)
            self.assertFalse(data["settings"]["vision_enabled"])
            self.assertFalse(data["settings"]["vision_from_environment"])
            _, again = asyncio.run(request("GET", "/api/search/settings"))
        self.assertFalse(again["settings"]["vision_enabled"])
        self.assertFalse(self.store.load_settings()["vision_enabled"])

    def test_patch_leaves_embedding_toggle_alone(self):
        asyncio.run(request("PATCH", "/api/search/settings", {"auto_index": False}))
        asyncio.run(request("PATCH", "/api/search/settings", {"vision_enabled": True}))
        settings = self.store.load_settings()
        self.assertFalse(settings["auto_index"])
        self.assertTrue(settings["vision_enabled"])

    def test_invalid_values_are_rejected_and_nothing_is_saved(self):
        for value in ("yes", 1, None):
            self.assertEqual(asyncio.run(request("PATCH", "/api/search/settings", {"vision_enabled": value}))[0], 422, value)
        self.assertIsNone(self.store.load_settings()["vision_enabled"])


if __name__ == "__main__":
    unittest.main()
