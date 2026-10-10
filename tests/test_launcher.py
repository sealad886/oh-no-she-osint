"""Prove the documented launcher bootstraps and hands off a usable local session."""

import http.client
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from local_osint.engine import Engine
from local_osint.server import LocalServer


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'fresh project with spaces'
        self.root.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copy2(source / 'start.sh', self.root / 'start.sh')
        shutil.copytree(source / 'local_osint', self.root / 'local_osint', ignore=shutil.ignore_patterns('__pycache__'))
        # Select the test interpreter without relying on the caller's system Python version.
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        shim = self.bin / 'python3'
        shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        shim.chmod(0o700)
        self.helper = Path(self.temp.name) / 'browser_handoff.py'
        self.helper.write_text('import os,sys\nfd=os.open(sys.argv[1],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)\nwith os.fdopen(fd,"w") as f:f.write(sys.argv[2])\n')

    def launch(self, options, name):
        log_path = Path(self.temp.name) / (name + '.log')
        opened = Path(self.temp.name) / (name + '-opened.txt')
        env = os.environ.copy()
        env['PATH'] = str(self.bin) + os.pathsep + env.get('PATH', '')
        env['BROWSER'] = shlex.join([sys.executable, str(self.helper), str(opened), '%s'])
        with log_path.open('x') as log:
            log_path.chmod(0o600)
            process = subprocess.Popen([str(self.root / 'start.sh'), *options], cwd=self.root.parent, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop, process)
        return process, log_path, opened

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def wait_for_launch(self, process, log_path, opened):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if opened.exists():
                # The browser helper creates its file before writing the URL.
                link = opened.read_text()
                if link:
                    return link
            if process.poll() is not None:
                self.fail('Launcher exited before browser handoff: ' + re.sub(r'http://\S+', '[private link]', log_path.read_text()))
            time.sleep(0.02)
        self.fail('Launcher did not hand off a local browser link within 30 seconds.')

    def check_session(self, link, network):
        parsed = urlsplit(link)
        self.assertEqual(parsed.hostname, '127.0.0.1')
        self.assertGreater(parsed.port, 0)
        token = parse_qs(parsed.fragment)['token'][0]
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            connection.request('GET', '/')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b'Evidence workspace', response.read())
            origin = 'http://' + parsed.netloc
            headers = {'Content-Type':'application/json', 'Origin':origin, 'X-OSINT-Local':'1'}
            connection.request('POST', '/api/session', json.dumps({'token':token}), headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            cookie = response.getheader('Set-Cookie').split(';', 1)[0]
            response.read()
            connection.request('GET', '/api/status', headers={**headers, 'Cookie':cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())['network'], network)
        finally:
            connection.close()

    def test_fresh_launcher_works_from_another_directory_without_port_argument(self):
        self.assertFalse((self.root / '.venv').exists())
        process, log, opened = self.launch([], 'fresh')
        link = self.wait_for_launch(process, log, opened)
        self.assertTrue((self.root / '.venv/bin/python').exists())
        self.assertTrue((self.root / '.local-osint/evidence.sqlite3').exists())
        self.check_session(link, True)
        self.stop(process)

    def test_launcher_preserves_explicit_busy_port_and_offline_override(self):
        engine = Engine(Path(self.temp.name) / 'occupied-data')
        with LocalServer(engine) as occupied:
            process, log, opened = self.launch(['--port', str(occupied.server_port)], 'busy')
            self.assertEqual(process.wait(timeout=30), 1)
            self.assertIn('--port 0', log.read_text())
            self.assertFalse(opened.exists())
            process, log, opened = self.launch(['--port', '0', '--offline'], 'offline')
            link = self.wait_for_launch(process, log, opened)
            self.check_session(link, False)
            self.assertNotEqual(urlsplit(link).port, occupied.server_port)
            self.stop(process)


if __name__ == '__main__':
    unittest.main()
