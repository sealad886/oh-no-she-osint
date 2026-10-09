import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from local_osint.engine import Engine, InvalidInput, MAX_BYTES, indicators, searchable_text


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.directory = Path(self.temp.name) / "private"
        self.engine = Engine(self.directory)
        self.case = self.engine.create_case("Project research")["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_end_to_end_provenance_search_relationship_and_export(self):
        raw = b"Public contact Hello@example.org https://example.org/project 192.0.2.1 2001:db8::1"
        saved = self.engine.ingest(self.case, raw, "Bulletin", "Public source")
        self.engine.ingest(self.case, b"Hello@example.org and archive.example.org", "Archive", "Other source")
        view = self.engine.view(self.case)
        self.assertEqual(view["counts"]["evidence"], 2)
        self.assertEqual({x["kind"] for x in view["entities"]}, {"email", "domain", "url", "ipv4", "ipv6"})
        email = next(x for x in view["entities"] if x["kind"] == "email")
        self.assertEqual(email["sources"], 2)
        detail = self.engine.entity(self.case, email["id"])
        self.assertEqual(len(detail["evidence"]), 2)
        self.assertTrue(any(x["value"] == "archive.example.org" for x in detail["related"]))
        evidence = self.engine.evidence(self.case, saved["id"])
        self.assertEqual(evidence["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(self.engine.view(self.case, "bulletin")["evidence"]), 1)
        self.assertEqual(len(self.engine.view(self.case, "Hello", "email")["entities"]), 1)
        exported = self.engine.export(self.case)
        original = exported["evidence"][0]
        self.assertEqual(base64.b64decode(original["original_base64"]), raw)
        self.assertEqual(original["id"], saved["id"])
        self.assertIn("Hello@example", self.engine.markdown(self.case))

    def test_restart_and_case_isolation(self):
        saved = self.engine.ingest(self.case, b"hello@example.org", "Note")
        other = self.engine.create_case("Other case")["id"]
        restarted = Engine(self.directory)
        self.assertEqual(restarted.view(self.case)["counts"]["evidence"], 1)
        self.assertEqual(restarted.view(other)["counts"]["evidence"], 0)
        entity = restarted.view(self.case)["entities"][0]
        with self.assertRaises(InvalidInput):
            restarted.evidence(other, saved["id"])
        with self.assertRaises(InvalidInput):
            restarted.entity(other, entity["id"])

    def test_deduplication_preserves_source_and_case(self):
        raw = b"example.org"
        first = self.engine.ingest(self.case, raw, "First", "Source A")
        again = self.engine.ingest(self.case, raw, "Changed title", "Source A")
        second = self.engine.ingest(self.case, raw, "Second", "Source B")
        self.assertEqual(first["id"], again["id"])
        self.assertTrue(again["duplicate"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.engine.evidence(self.case, first["id"])["title"], "First")

    def test_concurrent_duplicate_imports_are_atomic(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.engine.ingest(self.case, b"example.org", "Note", "same"), range(8)))
        self.assertEqual(len({x["id"] for x in results}), 1)
        self.assertEqual(self.engine.view(self.case)["counts"]["evidence"], 1)

    def test_rejects_invalid_evidence_without_partial_writes(self):
        for raw, format in [(b"", "text"), (b"\xff", "text"), (b"a\x00b", "text"), (b"{oops", "json"), (b"text", "pdf"), (b"x" * (MAX_BYTES + 1), "text")]:
            with self.subTest(format=format, length=len(raw)), self.assertRaises(InvalidInput):
                self.engine.ingest(self.case, raw, "Bad", format=format)
        self.assertEqual(self.engine.view(self.case)["counts"]["evidence"], 0)
        with self.assertRaises(InvalidInput):
            self.engine.ingest(self.case, (b"example.org " * 10001), "Too many")
        self.assertEqual(self.engine.view(self.case)["counts"]["entities"], 0)

    def test_all_import_formats_and_html_is_inert(self):
        html = b'<h1>Contact</h1><a href="https://example.org/path">hello@example.org</a><script>secret.example.com</script><style>hidden.example.com</style>'
        saved = self.engine.ingest(self.case, html, "Page", format="html")
        text = self.engine.evidence(self.case, saved["id"])["text"]
        self.assertIn("https://example.org/path", text)
        self.assertNotIn("secret.example", text)
        self.assertNotIn("hidden.example", text)
        self.engine.ingest(self.case, b'{"contact":"hello@example.org"}', "JSON", format="json")
        self.engine.ingest(self.case, b"name,contact\nproject,hello@example.org", "CSV", format="csv")
        self.assertEqual(self.engine.view(self.case)["counts"]["evidence"], 3)

    def test_normalization_and_offsets(self):
        text = "Hi Hello@EXAMPLE.ORG https://EXAMPLE.org/path?token=secret#x. 999.999.1.1 2001:0db8::1"
        found = indicators(text)
        self.assertTrue(any(x["value"] == "Hello@example.org" for x in found))
        self.assertTrue(any(x["value"] == "https://example.org/path" for x in found))
        self.assertTrue(any(x["value"] == "2001:db8::1" for x in found))
        self.assertFalse(any(x["kind"] == "ipv4" for x in found))
        self.assertTrue(all(0 <= x["start"] < x["end"] <= len(text) for x in found))
        self.assertFalse(any(x["kind"] == "url" for x in indicators("https://user:secret@example.org/")))

    def test_urls_preserve_balanced_delimiters_and_exclude_filename_domains(self):
        text = "(https://en.wikipedia.org/wiki/Example_(topic)). https://example.org/report.pdf?next=document.title script.version example.zip"
        found = indicators(text)
        urls = [item for item in found if item["kind"] == "url"]
        self.assertIn("https://en.wikipedia.org/wiki/Example_(topic)", [item["value"] for item in urls])
        for item in urls:
            self.assertFalse(text[item["start"]:item["end"]].endswith("))."))
        domains = {item["value"] for item in found if item["kind"] == "domain"}
        self.assertEqual(domains, {"en.wikipedia.org", "example.org", "example.zip"})
        ipv6_url = "https://[2606:4700:4700::1111]/"
        self.assertIn(ipv6_url, [x["value"] for x in indicators(ipv6_url) if x["kind"] == "url"])

    def test_repeated_mentions_count_distinct_shared_sources(self):
        self.engine.ingest(self.case, ("example.org " * 4999 + "other.org").encode(), "Repeated mentions")
        self.engine.ingest(self.case, b"example.org other.org third.org", "Second source")
        entity = next(x for x in self.engine.view(self.case)["entities"] if x["value"] == "example.org")
        related = {x["value"]:x["shared_sources"] for x in self.engine.entity(self.case, entity["id"])["related"]}
        self.assertEqual(related, {"other.org": 2, "third.org": 1})

    def test_search_is_literal_and_sql_injection_is_inert(self):
        self.engine.ingest(self.case, b"100% verified _ note", "Notes")
        self.assertEqual(len(self.engine.view(self.case, "%")["evidence"]), 1)
        self.assertEqual(len(self.engine.view(self.case, "' OR 1=1 --")["evidence"]), 0)
        self.assertEqual(len(self.engine.cases()), 1)

    def test_markdown_cannot_embed_html_or_links(self):
        self.engine.ingest(self.case, b"example.org", "<img src=x onerror=alert(1)> [click](https://evil.example)", "![image](https://evil.example)")
        report = self.engine.markdown(self.case)
        self.assertNotIn("<img", report)
        self.assertNotIn("![image]", report)
        self.assertNotIn("[click](", report)

    def test_backup_restore_and_no_overwrite(self):
        self.engine.ingest(self.case, b"hello@example.org", "Original")
        destination = Path(self.temp.name) / "backup.sqlite3"
        self.engine.backup(destination)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        restore_dir = Path(self.temp.name) / "restored"
        restore_dir.mkdir(mode=0o700)
        restored = restore_dir / "evidence.sqlite3"
        restored.write_bytes(destination.read_bytes())
        restored.chmod(0o600)
        self.assertEqual(Engine(restore_dir).export(self.case)["evidence"], self.engine.export(self.case)["evidence"])
        with self.assertRaises(FileExistsError):
            self.engine.backup(destination)

    def test_private_storage_permissions_and_symlink_refusal(self):
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.engine.path.stat().st_mode & 0o777, 0o600)
        link = Path(self.temp.name) / "linked"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(InvalidInput):
            Engine(link)
        self.engine.path.chmod(0o644)
        with self.assertRaises(InvalidInput):
            Engine(self.directory)

    def test_unknown_schema_is_not_modified(self):
        with sqlite3.connect(self.engine.path) as db:
            db.execute("PRAGMA user_version=999")
        with self.assertRaises(InvalidInput):
            Engine(self.directory)
        with sqlite3.connect(self.engine.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 999)


if __name__ == "__main__":
    unittest.main()
