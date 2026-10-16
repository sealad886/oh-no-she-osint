"""Unexpected failures remain useful locally without retaining private inputs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_osint.diagnostics import Diagnostics, MAX_EVENTS


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_bounded_private_events_survive_restart_without_error_text(self):
        diagnostics = Diagnostics(self.directory)
        for _ in range(MAX_EVENTS + 5):
            entry = diagnostics.record('http_request', RuntimeError('private-secret https://private.invalid/?token=secret'))
        self.assertEqual(len(diagnostics.recent()), MAX_EVENTS)
        self.assertEqual(set(entry), {'id', 'at', 'operation', 'exception'})
        self.assertEqual(entry['exception'], 'RuntimeError')
        self.assertEqual(diagnostics.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('private', diagnostics.path.read_text())
        reopened = Diagnostics(self.directory)
        self.assertEqual(reopened.recent(), diagnostics.recent())
        self.assertEqual(reopened.recent()[0]['id'], entry['id'])

    def test_unknown_metadata_and_loaded_extra_fields_cannot_leak(self):
        class PrivateSecret(Exception):
            pass
        diagnostics = Diagnostics(self.directory)
        event = diagnostics.record('private-query', PrivateSecret('private-message'))
        self.assertEqual(event['operation'], 'http_request')
        self.assertEqual(event['exception'], 'Exception')
        diagnostics.path.write_text(json.dumps([event, {**event, 'message': 'private-message'}]))
        self.assertEqual(Diagnostics(self.directory).recent(), [event])

    def test_failed_persistence_keeps_reference_in_memory_and_recovers(self):
        diagnostics = Diagnostics(self.directory)
        with patch('local_osint.diagnostics.write_private_json', side_effect=OSError('private disk path')):
            event = diagnostics.record('investigation', RuntimeError('private clue'))
        self.assertFalse(diagnostics.persistence_available)
        self.assertEqual(diagnostics.recent(), [event])
        diagnostics.record('http_request', KeyError('private key'))
        self.assertTrue(diagnostics.persistence_available)
        self.assertEqual(len(Diagnostics(self.directory).recent()), 2)

    def test_symlink_destination_is_never_read_or_overwritten(self):
        target = self.directory / 'secret.json'
        target.write_text('private content')
        (self.directory / 'diagnostics.json').symlink_to(target)
        diagnostics = Diagnostics(self.directory)
        self.assertFalse(diagnostics.persistence_available)
        event = diagnostics.record('http_request', RuntimeError('private'))
        self.assertEqual(diagnostics.recent(), [event])
        self.assertFalse(diagnostics.persistence_available)
        self.assertEqual(target.read_text(), 'private content')

    def test_corrupt_or_oversized_saved_diagnostics_do_not_prevent_startup(self):
        for contents in ['{bad json', 'x' * 65537, '{}']:
            with self.subTest(contents=contents[:12]):
                (self.directory / 'diagnostics.json').write_text(contents)
                diagnostics = Diagnostics(self.directory)
                self.assertFalse(diagnostics.persistence_available)
                self.assertEqual(diagnostics.recent(), [])
                diagnostics.record('http_request', RuntimeError())
                self.assertTrue(diagnostics.persistence_available)
