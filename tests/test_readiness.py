"""Readiness is a local observation, never permission to probe or install."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from local_osint.connectors import catalogue
from local_osint.engine import Engine, InvalidInput
from local_osint.readiness import report
from local_osint.workspace import Workspace


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.tmp.name) / 'private')
        self.case = self.engine.create_case('Public fixture', 'topic')['id']

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, result, identifier='github_public'):
        return next(item for item in result['sources'] if item['id'] == identifier)

    def test_inspection_never_launches_or_probes_and_keeps_runtime_state_recorded(self):
        model = Path(self.tmp.name) / 'model.gguf'
        model.write_bytes(b'GGUFcached-model')
        ai = SimpleNamespace(model_path=model, _lock=threading.RLock(), _state='loading',
                             status=Mock(side_effect=AssertionError('AI probe')))
        service = SimpleNamespace(lock=threading.RLock(), state={'image_id': 'sha256:' + 'a' * 64,
                                  'container': 'owned-record'}, phase='', stopping=False, last_error='',
                                  status=Mock(side_effect=AssertionError('Docker probe')))
        with ExitStack() as stack:
            for name in ('socket.socket', 'socket.getaddrinfo', 'socket.create_connection',
                         'subprocess.Popen', 'subprocess.run'):
                stack.enter_context(patch(name, side_effect=AssertionError(name)))
            stack.enter_context(patch('local_osint.readiness.shutil.which', return_value='/installed/tool'))
            result = report(self.engine, case_id=self.case, allow_network=True, ai=ai, search_service=service)
        self.assertTrue(result['network_permitted'])
        self.assertEqual(next(x for x in result['components'] if x['id'] == 'local_ai')['runtime_state'], 'loading')
        search = next(x for x in result['components'] if x['id'] == 'search_service')
        self.assertTrue(search['configured'])
        self.assertEqual(search['state'], 'unverified')
        self.assertEqual(self.source(result)['health']['state'], 'unverified')
        ai.status.assert_not_called()
        service.status.assert_not_called()

    def test_offline_and_unverified_are_separate_from_installed_prerequisites(self):
        with patch('local_osint.readiness.cached_model', return_value=None), patch('local_osint.readiness.shutil.which', return_value=None):
            offline = report(self.engine, case_id=self.case)
            enabled = report(self.engine, case_id=self.case, allow_network=True)
        remote = self.source(offline)
        self.assertTrue(remote['implementation_available'])
        self.assertTrue(remote['prerequisites_ready'])
        self.assertEqual(remote['state'], 'offline')
        self.assertFalse(remote['available'])
        self.assertEqual(remote['health']['state'], 'unverified')
        self.assertTrue(self.source(offline, 'email_headers')['available'])
        self.assertFalse(self.source(offline, 'email_headers')['network_required'])
        self.assertEqual(self.source(enabled)['state'], 'unverified')
        self.assertTrue(self.source(enabled)['available'])
        self.assertFalse(self.source(enabled, 'searxng')['prerequisites_ready'])

    def test_case_health_cooldowns_staleness_and_case_isolation_do_not_change_saved_state(self):
        workspace = Workspace(self.engine)
        retry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        workspace.update_health(self.case, 'github_public', 'rate_limited', retry_at=retry, error='private query detail')
        other = self.engine.create_case('Other fixture')['id']
        before = workspace.health(self.case)
        result = report(self.engine, case_id=self.case, allow_network=True)
        source = self.source(result)
        self.assertEqual(source['state'], 'cooldown')
        self.assertFalse(source['available'])
        self.assertEqual(source['health']['retry_at'], retry)
        self.assertIsNotNone(source['health']['checked_at'])
        self.assertNotIn('private query detail', json.dumps(result))
        self.assertEqual(workspace.health(self.case), before)
        self.assertEqual(self.source(report(self.engine, case_id=other))['health']['state'], 'unverified')
        self.assertEqual(self.source(report(self.engine))['health']['state'], 'unverified')
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with self.engine.connection() as db:
            db.execute('UPDATE workspace_health SET updated_at=?, retry_at=NULL WHERE case_id=?', (old, self.case))
        self.assertEqual(self.source(report(self.engine, case_id=self.case))['health']['state'], 'stale')
        workspace.update_health(self.case, 'github_public', 'ready')
        self.assertEqual(self.source(report(self.engine, case_id=self.case))['health']['state'], 'last_success')
        with self.assertRaises(InvalidInput):
            report(self.engine, case_id='missing')

    def test_persisted_search_setup_is_local_unverified_and_bad_metadata_is_actionable(self):
        directory = self.engine.directory / 'search-service'
        directory.mkdir()
        path = directory / 'state.json'
        path.write_text(json.dumps({'image_id': 'sha256:' + 'b' * 64, 'container': 'recorded'}))
        with patch('local_osint.readiness.shutil.which', return_value='/installed/docker'):
            component = next(x for x in report(self.engine)['components'] if x['id'] == 'search_service')
            self.assertTrue(component['prerequisites_ready'])
            self.assertTrue(component['owned_container'])
            self.assertEqual(component['state'], 'unverified')
            path.write_text('{broken')
            component = next(x for x in report(self.engine)['components'] if x['id'] == 'search_service')
            self.assertFalse(component['prerequisites_ready'])
            self.assertIn('recover', component['message'])

    def test_catalogue_uses_same_source_projection_without_claiming_live_readiness(self):
        result = report(self.engine, case_id=self.case, allow_network=False)
        entry = next(x for x in catalogue(readiness=result) if x['id'] == 'github_public')
        self.assertEqual(entry['readiness'], self.source(result))
        self.assertEqual(entry['availability'], 'offline')
        self.assertFalse(entry['available'])
        default = next(x for x in catalogue() if x['id'] == 'github_public')
        self.assertEqual(default['availability'], 'unverified')


if __name__ == '__main__':
    unittest.main()
