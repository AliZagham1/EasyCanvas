import unittest

from backend.app.content_discovery import build_file_inventory


def record(kind, record_id, **metadata):
    return {
        "source_type": kind, "canvas_id": record_id,
        "title": metadata.get("title", "Source"), "metadata": metadata,
        "content_id": metadata.get("content_id"), "item_type": metadata.get("type"),
        "module_id": metadata.get("module_id"), "module_name": metadata.get("module_name"),
    }


class ContentDiscoveryTests(unittest.TestCase):
    def test_same_file_keeps_all_references_and_listing_metadata(self):
        snapshot = {
            "course_id": 42,
            "syllabus": record("syllabus", 42, syllabus_body='<a href="/courses/42/files/9/download">Syllabus</a>'),
            "assignments": [record("assignment", 11, description='<a data-api-endpoint="https://canvas.example/api/v1/files/9" href="/files/9/download">Read</a>')],
            "module_items": [record("module_item", 20, type="File", content_id=9, module_id=7)],
            "files": [record("file", 9, display_name="Reading.pdf", url="https://storage.example/signed")],
        }
        inventory = build_file_inventory(snapshot, "https://canvas.example")
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["file_id"], 9)
        self.assertEqual(inventory[0]["title"], "Reading.pdf")
        self.assertEqual(len(inventory[0]["references"]), 4)
        self.assertEqual(inventory[0]["references"][2]["module_id"], 7)

    def test_files_are_found_outside_modules_when_files_index_is_denied(self):
        snapshot = {
            "course_id": 42,
            "source_status": {"files": {"status": "unavailable", "http_status": 403}},
            "pages": [record("page", 1, body='<iframe src="/courses/42/files/9/preview"></iframe>')],
            "announcements": [record("announcement", 2, message='<a href="/api/v1/courses/42/files/10">Read</a>')],
            "discussions": [record("discussion", 3, message='<a href="/files/11?download=1">Read</a>')],
        }
        self.assertEqual([item["file_id"] for item in build_file_inventory(snapshot, "https://canvas.example")], [9, 10, 11])

    def test_external_tools_and_foreign_hosts_are_not_followed(self):
        html = '''<a href="https://evil.example/courses/42/files/9">External</a>
        <a href="https://canvas.example.evil.test/files/10">External</a>
        <a href="javascript:alert(1)">JS</a><a href="https://gradescope.example/">Tool</a>'''
        snapshot = {"course_id": 42, "pages": [record("page", 1, body=html)]}
        self.assertEqual(build_file_inventory(snapshot, "https://canvas.example"), [])

    def test_relative_links_entities_and_string_ids(self):
        snapshot = {
            "course_id": 42,
            "assignments": [record("assignment", 1, description='<a href="/files/9/download?x=1&amp;y=2">Read</a>')],
            "module_items": [record("module_item", 2, type="File", content_id="9")],
        }
        inventory = build_file_inventory(snapshot, "https://canvas.example")
        self.assertEqual([item["file_id"] for item in inventory], [9])
        self.assertEqual(len(inventory[0]["references"]), 2)

    def test_invalid_ids_and_empty_sources_are_ignored(self):
        snapshot = {"course_id": 42, "module_items": [record("module_item", i, type="File", content_id=value) for i, value in enumerate([None, True, -1, "bad"])]}
        self.assertEqual(build_file_inventory(snapshot, "https://canvas.example"), [])
        self.assertEqual(build_file_inventory({"course_id": 42}, "https://canvas.example"), [])

    def test_attachments_and_good_links_survive_a_malformed_link(self):
        snapshot = {"course_id": 42, "announcements": [record(
            "announcement", 1,
            message='<a href="https://[broken/files/9">Broken</a><a href="/files/10">Reading</a>',
            attachments=[{"id": 11, "display_name": "Notes.pdf", "url": "https://storage.example/signed"}],
        )]}
        inventory = build_file_inventory(snapshot, "https://canvas.example")
        self.assertEqual({item["file_id"] for item in inventory}, {10, 11})
        attachment = next(item for item in inventory if item["file_id"] == 11)
        self.assertEqual(attachment["title"], "Notes.pdf")
        self.assertEqual(attachment["references"][0]["source_type"], "announcement")


if __name__ == "__main__":
    unittest.main()
