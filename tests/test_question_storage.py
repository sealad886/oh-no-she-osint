"""Question, identity and source provenance contracts for schema 3."""

import hashlib
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from local_osint.engine import Engine, InvalidInput
from local_osint.workspace import Workspace


class QuestionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.directory = Path(self.temporary.name) / "data"
        self.engine = Engine(self.directory)
        self.workspace = Workspace(self.engine)
        self.case = self.engine.create_case("Ada Example", "person")["id"]
        self.other = self.engine.create_case("Other target", "person")["id"]
        self.source = self.engine.ingest(self.case, b"Ada Example works with Harbour Community.", "Public bulletin", "https://example.org/bulletin")["id"]
        self.candidate = self.workspace.candidate(self.case, "Ada Example", "Community role", self.source)["id"]

    def tearDown(self):
        self.temporary.cleanup()

    def test_question_progress_survives_restart_and_deduplicates_without_reset(self):
        first = self.workspace.question(self.case, "Which organization is associated with Ada?", key="affiliation", gap="Need a first-party source.")
        self.workspace.update_question(self.case, first["id"], status="supported", evidence_ids=[self.source], gap="")
        repeated = self.workspace.question(self.case, "Which organization is associated with Ada?", key="affiliation")
        self.assertEqual(repeated["id"], first["id"])
        self.assertEqual(repeated["status"], "supported")
        restored = Workspace(Engine(self.directory)).questions(self.case)
        self.assertEqual(restored[0]["evidence_ids"], [self.source])
        with self.assertRaises(InvalidInput):
            self.workspace.update_question(self.other, first["id"], status="supported")
        with self.assertRaises(InvalidInput):
            self.workspace.question(self.other, "Foreign reference", evidence_ids=[self.source])

    def test_identity_decision_is_reversible_and_never_merges_namesakes(self):
        namesake = self.workspace.candidate(self.case, "Ada Example", "Different city")
        first_claim = self.workspace.observe(self.case, self.source, self.candidate, "Ada Example works with Harbour Community.", "affiliation", "Harbour Community")
        for status in ("same", "different", "uncertain"):
            self.workspace.decide_candidate(self.case, self.candidate, status, "Operator comparison")
        snapshot = self.workspace.snapshot(self.case)
        self.assertEqual([item["status"] for item in snapshot["identity_decisions"]], ["same", "different", "uncertain"])
        self.assertEqual({item["id"] for item in snapshot["candidates"]}, {namesake["id"], self.candidate})
        self.assertEqual(snapshot["claims"][0]["id"], first_claim["id"])
        self.assertEqual(snapshot["claims"][0]["candidate_id"], self.candidate)
        with self.assertRaises(InvalidInput):
            self.workspace.decide_candidate(self.other, self.candidate, "same")

    def test_shared_dated_propositions_keep_exact_support_and_contradiction(self):
        first = self.workspace.observe(self.case, self.source, self.candidate, "Ada Example works with Harbour Community.", "Affiliation", "Harbour Community", observed_at="2026")
        second_source = self.engine.ingest(self.case, b"Harbour Community lists Ada Example as staff.", "Staff directory", "https://example.net/staff")["id"]
        same = self.workspace.observe(self.case, second_source, self.candidate, "Harbour Community lists Ada Example as staff.", " affiliation ", "HARBOUR  COMMUNITY", observed_at="2026")
        self.assertEqual(first["id"], same["id"])
        self.assertEqual(len(same["references"]), 2)
        earlier = self.workspace.observe(self.case, self.source, self.candidate, "Ada Example works with Harbour Community.", "Affiliation", "Harbour Community", observed_at="2025")
        self.assertNotEqual(first["id"], earlier["id"])
        other_identity = self.workspace.candidate(self.case, "Ada Example", "Namesake")
        other_claim = self.workspace.observe(self.case, second_source, other_identity["id"], "Harbour Community lists Ada Example as staff.", "Affiliation", "Harbour Community", observed_at="2026")
        self.assertNotEqual(first["id"], other_claim["id"])
        correction = self.engine.ingest(self.case, b"Ada Example is no longer staff.", "Correction", "https://example.net/correction")["id"]
        disputed = self.workspace.observe(self.case, correction, self.candidate, "Ada Example is no longer staff.", claim_id=first["id"], relation="contradict")
        self.assertEqual(disputed["status"], "disputed")
        self.assertEqual(len(disputed["references"]), 3)
        self.assertTrue(all(reference["collected_at"] for reference in disputed["references"]))

    def test_copies_archives_unknown_origins_and_identical_bytes_do_not_corroborate(self):
        originals = [self.source]
        for index, text in enumerate(("Copied: Ada at Harbour Community.", "Archive: Ada at Harbour Community.", "Unknown: Ada at Harbour Community.", "Independent: Ada at Harbour Community.")):
            originals.append(self.engine.ingest(self.case, text.encode(), "Source", f"https://source{index}.org/ada")["id"])
        self.workspace.origin(self.case, originals[0], "original", reason="Public first-party bulletin")
        self.workspace.origin(self.case, originals[1], "syndicated", source_id=originals[0])
        self.workspace.origin(self.case, originals[2], "archive", source_id=originals[0])
        claim = None
        for source in originals[:4]:
            claim = self.workspace.observe(self.case, source, self.candidate, self.engine.evidence(self.case, source)["text"], "affiliation", "Harbour Community", "supported")
        self.assertEqual(claim["independent_origins"], 1)
        self.assertFalse(claim["corroborated"])
        self.workspace.origin(self.case, originals[4], "original", reason="Independently published record")
        claim = self.workspace.observe(self.case, originals[4], self.candidate, self.engine.evidence(self.case, originals[4])["text"], "affiliation", "Harbour Community", "supported")
        self.assertTrue(claim["corroborated"])
        self.assertEqual(self.workspace.snapshot(self.case)["counts"]["corroborated_claims"], 1)
        with self.assertRaises(InvalidInput):
            self.workspace.origin(self.case, originals[0], "copy", source_id=originals[1])
        identical = self.engine.ingest(self.case, self.engine.evidence(self.case, self.source)["text"].encode(), "Republished", "https://copy.org/bulletin")["id"]
        self.workspace.origin(self.case, identical, "original")
        same = self.workspace.observe(self.case, identical, self.candidate, self.engine.evidence(self.case, identical)["text"], "duplicate", "same bytes", "supported")
        same = self.workspace.observe(self.case, self.source, self.candidate, self.engine.evidence(self.case, self.source)["text"], "duplicate", "same bytes", "supported")
        self.assertEqual(same["independent_origins"], 1)

    def test_same_target_projection_reconciles_sources_and_undo_restores_original_claims(self):
        second_source = self.engine.ingest(self.case, b"Harbour Community employs Ada Example.", "Staff record", "https://independent.example/record")["id"]
        other_candidate = self.workspace.candidate(self.case, "Ada Example", "Staff record", second_source)["id"]
        claims = [self.workspace.observe(self.case, source, candidate, self.engine.evidence(self.case, source)["text"], "Affiliation", "Harbour Community", "supported", observed_at="2026") for source, candidate in ((self.source, self.candidate), (second_source, other_candidate))]
        for source in (self.source, second_source):
            self.workspace.origin(self.case, source, "original")
        with self.engine.connection() as db:
            original_rows = [tuple(row) for row in db.execute("SELECT * FROM workspace_claims ORDER BY id")]
        self.workspace.decide_candidate(self.case, self.candidate, "same")
        self.workspace.decide_candidate(self.case, other_candidate, "same")
        projected = self.workspace.snapshot(self.case)["claims"]
        self.assertEqual(len(projected), 1)
        self.assertEqual(set(projected[0]["member_claim_ids"]), {claim["id"] for claim in claims})
        self.assertEqual(set(projected[0]["candidate_ids"]), {self.candidate, other_candidate})
        self.assertEqual({reference["evidence_id"] for reference in projected[0]["references"]}, {self.source, second_source})
        self.assertTrue(projected[0]["corroborated"])
        self.workspace.decide_candidate(self.case, other_candidate, "uncertain")
        restored = self.workspace.snapshot(self.case)["claims"]
        self.assertEqual({claim["id"] for claim in restored}, {claim["id"] for claim in claims})
        self.assertTrue(all(len(claim["references"]) == 1 and not claim["corroborated"] for claim in restored))
        with self.engine.connection() as db:
            self.assertEqual(original_rows, [tuple(row) for row in db.execute("SELECT * FROM workspace_claims ORDER BY id")])

    def test_namesakes_and_dated_propositions_never_project_together(self):
        namesake = self.workspace.candidate(self.case, "Ada Example", "Other city")["id"]
        for candidate in (self.candidate, namesake):
            for year in ("2025", "2026"):
                self.workspace.observe(self.case, self.source, candidate, self.engine.evidence(self.case, self.source)["text"], "affiliation", "Harbour Community", observed_at=year)
        self.workspace.decide_candidate(self.case, self.candidate, "same")
        self.workspace.decide_candidate(self.case, namesake, "different")
        self.assertEqual(len(self.workspace.snapshot(self.case)["claims"]), 4)
        self.workspace.decide_candidate(self.case, namesake, "same")
        projected = self.workspace.snapshot(self.case)["claims"]
        self.assertEqual(len(projected), 2)
        self.assertEqual({claim["observed_at"] for claim in projected}, {"2025", "2026"})
        self.assertTrue(all(len(claim["references"]) == 1 for claim in projected))

    def test_target_projection_preserves_disputed_status_and_origin_guards(self):
        namesake = self.workspace.candidate(self.case, "Ada Example", "Other record")["id"]
        for candidate, relation in ((self.candidate, "support"), (namesake, "contradict")):
            self.workspace.observe(self.case, self.source, candidate, self.engine.evidence(self.case, self.source)["text"], "affiliation", "Harbour Community", "supported", relation=relation)
            self.workspace.decide_candidate(self.case, candidate, "same")
        projected = self.workspace.snapshot(self.case)["claims"]
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["status"], "disputed")
        self.assertEqual({reference["relation"] for reference in projected[0]["references"]}, {"support", "contradict"})
        for kind in ("unknown", "original"):
            with self.assertRaises(InvalidInput):
                self.workspace.origin(self.case, self.source, kind, source_id=self.source)
        other_source = self.engine.ingest(self.other, b"Other case source.", "Other")["id"]
        with self.assertRaises(InvalidInput):
            self.workspace.origin(self.case, self.source, "copy", source_id=other_source)

    def test_page_provenance_reaches_late_passages_without_original_mutation(self):
        prefix = "Unrelated appendix. " * 700
        text = prefix + "Ada Example attended Harbour Symposium."
        source = self.engine.ingest(self.case, text.encode(), "Long programme", "https://example.org/programme")["id"]
        pages = [{"page": 1, "start": 0, "end": len(prefix), "method": "text"}, {"page": 18, "start": len(prefix), "end": len(text), "method": "ocr"}]
        before = self.engine.evidence(self.case, source)
        self.workspace.record_extraction(self.case, source, text, "pdf-v1", pages)
        claim = self.workspace.observe(self.case, source, self.candidate, "Ada Example attended Harbour Symposium.", "event", "Harbour Symposium")
        reference = claim["references"][0]
        self.assertEqual(reference["pages"][0]["page"], 18)
        self.assertEqual(reference["extractor_version"], "pdf-v1")
        retrieved = self.workspace.retrieve(self.case, "Symposium")[0]
        self.assertIn(18, [page["page"] for page in retrieved["pages"]])
        self.assertIn("Harbour Symposium", retrieved["text"])
        self.assertEqual(self.engine.evidence(self.case, source), before)
        revised = "OCR corrected late signal: Harbour Symposium speaker."
        self.workspace.record_extraction(self.case, source, revised, "pdf-ocr-v2", [{"page": 18, "start": 0, "end": len(revised), "method": "ocr"}])
        self.assertEqual(self.workspace.retrieve(self.case, "corrected")[0]["text"], revised)
        self.assertEqual(self.engine.evidence(self.case, source), before)
        exported = self.engine.export(self.case)
        versions = {item['extractor_version']: item for item in exported['extractions'] if item['evidence_id'] == source}
        self.assertEqual(versions['pdf-v1']['text'], text)
        self.assertEqual(versions['pdf-ocr-v2']['text'], revised)
        self.assertEqual(versions['pdf-ocr-v2']['pages'][0]['page'], 18)
        self.assertEqual(versions['pdf-v1']['evidence_sha256'], before['sha256'])
        with self.assertRaises(InvalidInput):
            self.workspace.record_extraction(self.case, source, text, "pdf-v1", [])

    def test_v2_upgrade_backs_up_and_preserves_every_original_table(self):
        tables = ("cases", "evidence", "entities", "mentions")
        with self.engine.connection() as db:
            before = {table: [tuple(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")] for table in tables}
            for table in ("workspace_questions", "workspace_identity_decisions", "workspace_origins", "workspace_document_pages"):
                db.execute("DROP TABLE " + table)
            db.execute("ALTER TABLE workspace_claims DROP COLUMN normalized_key")
            db.execute("ALTER TABLE workspace_claims DROP COLUMN observed_at")
            db.execute("PRAGMA user_version=2")
        upgraded = Engine(self.directory)
        with upgraded.connection() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(before, {table: [tuple(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")] for table in tables})
        backups = list((self.directory / "backups").glob("schema2-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        with sqlite3.connect(backups[0]) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(before, {table: db.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall() for table in tables})
        Engine(self.directory)
        self.assertEqual(len(list((self.directory / "backups").glob("*.sqlite3"))), 1)


if __name__ == "__main__":
    unittest.main()
