import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock

from local_osint.engine import Engine
from local_osint.server import LocalServer


class PageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.server = None
        self.thread = None

    def start(self, enabled=True, timeout=0.4, grace=2, close_grace=0.05):
        self.server = LocalServer(self.engine, shutdown_on_close=enabled, page_timeout=timeout, launch_grace=grace,
                                  close_grace=close_grace)
        self.jobs_closed, self.ai_stopped = Mock(), Mock()
        self.server.jobs.close = self.jobs_closed
        self.server.ai.stop = self.ai_stopped
        def serve():
            try:
                self.server.serve_forever(poll_interval=0.01)
            finally:
                self.server.server_close()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, action, page_id=None, authenticated=True, headers=None, data=None):
        body = {'id': page_id or str(uuid.uuid4())} if data is None else data
        request_headers = {'Origin': self.server.origin, 'X-OSINT-Local': '1', 'Content-Type': 'application/json'}
        if authenticated:
            request_headers['Cookie'] = 'osint_session=' + self.server.token
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request('POST', '/api/page/' + action, json.dumps(body), request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def assert_shutdown(self):
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        self.jobs_closed.assert_called_once()
        self.ai_stopped.assert_called_once()

    def test_final_registered_page_close_tears_down_owned_runtime(self):
        self.start()
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertEqual(self.request('open', first)[0], 200)
        self.assertEqual(self.request('open', second)[0], 200)
        self.assertEqual(self.request('close', first)[1]['pages'], 1)
        self.assertTrue(self.thread.is_alive())
        self.jobs_closed.assert_not_called()
        self.ai_stopped.assert_not_called()
        self.assertFalse(self.request('close', second)[1]['registered'])
        self.assert_shutdown()
        self.server.server_close()
        self.jobs_closed.assert_called_once()

    def test_unknown_close_and_heartbeat_do_not_authorize_shutdown(self):
        self.start()
        self.assertEqual(self.request('close')[0], 200)
        self.assertFalse(self.request('heartbeat')[1]['registered'])
        self.assertTrue(self.thread.is_alive())
        page = str(uuid.uuid4())
        self.request('open', page)
        self.request('close')
        self.assertTrue(self.thread.is_alive())
        self.request('close', page)
        self.assert_shutdown()

    def test_page_can_reopen_while_another_page_owns_runtime(self):
        self.start()
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        self.request('open', first)
        self.request('open', second)
        self.request('close', first)
        self.assertEqual(self.request('open', first)[1]['pages'], 2)
        self.request('close', second)
        self.assertTrue(self.thread.is_alive())
        self.request('close', first)
        self.assert_shutdown()

    def test_refresh_registration_cancels_pending_final_page_shutdown(self):
        self.start(timeout=2, close_grace=0.2)
        original, refreshed = str(uuid.uuid4()), str(uuid.uuid4())
        self.request('open', original)
        self.request('close', original)
        time.sleep(0.05)
        self.assertTrue(self.request('open', refreshed)[1]['registered'])
        time.sleep(0.25)
        self.assertTrue(self.thread.is_alive())
        self.jobs_closed.assert_not_called()
        self.request('close', refreshed)
        self.assert_shutdown()

    def test_heartbeat_renews_lease_then_expiry_tears_down_runtime(self):
        self.start(timeout=0.25)
        page = str(uuid.uuid4())
        self.request('open', page)
        for _ in range(4):
            time.sleep(0.08)
            self.assertTrue(self.request('heartbeat', page)[1]['registered'])
            self.assertTrue(self.thread.is_alive())
        self.assert_shutdown()

    def test_initial_launch_grace_expires_without_registered_browser(self):
        self.start(grace=0.15)
        self.assert_shutdown()

    def test_unauthenticated_or_cross_origin_close_cannot_end_session(self):
        self.start()
        page = str(uuid.uuid4())
        self.request('open', page)
        for authenticated, headers, expected in [(False, {}, 401), (True, {'Origin': 'https://evil.example'}, 403),
                                                  (True, {'X-OSINT-Local': ''}, 401), (True, {'Host': 'evil.example'}, 403)]:
            self.assertEqual(self.request('close', page, authenticated, headers)[0], expected)
            self.assertTrue(self.thread.is_alive())
        self.request('close', page)
        self.assert_shutdown()

    def test_invalid_ids_and_bounded_page_count_are_rejected(self):
        self.start(timeout=3)
        for body in [{'id': 'x' * 1000}, {'id': 1}, {'id': str(uuid.uuid4()), 'extra': True}, {}]:
            self.assertEqual(self.request('open', data=body)[0], 400)
        pages = [str(uuid.uuid4()) for _ in range(32)]
        for page in pages:
            self.assertEqual(self.request('open', page)[0], 200)
        self.assertEqual(self.request('open')[0], 400)
        self.assertEqual(self.request('open', pages[0])[1]['pages'], 32)
        for page in pages:
            self.request('close', page)
        self.assert_shutdown()

    def test_default_server_does_not_stop_on_close_or_expiry(self):
        self.start(enabled=False, timeout=0.05, grace=0.05)
        page = str(uuid.uuid4())
        self.request('open', page)
        self.assertFalse(self.request('close', page)[1]['closing'])
        time.sleep(0.1)
        self.assertTrue(self.thread.is_alive())
        self.jobs_closed.assert_not_called()
        self.ai_stopped.assert_not_called()


if __name__ == '__main__':
    unittest.main()
