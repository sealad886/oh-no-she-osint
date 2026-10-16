"""Verify actual proxy authentication, private runtime ownership and teardown."""

import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_osint.engine import Engine, InvalidInput
from local_osint.nginx import NginxProxy, nginx_binary
from local_osint.server import LocalServer, MAX_REQUEST


try:
    NGINX = nginx_binary()
except InvalidInput:
    NGINX = None


class NginxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / "private data with spaces")

    def test_missing_binary_and_bad_ports_are_actionable_before_process_creation(self):
        with patch("local_osint.nginx.shutil.which", return_value=None), patch("local_osint.nginx.Path.is_file", return_value=False), patch("local_osint.nginx.subprocess.Popen") as process:
            with self.assertRaisesRegex(InvalidInput, "Install nginx"):
                nginx_binary()
            process.assert_not_called()
        for backend, port in [(0, 0), (True, 0), (9000, -1), (9000, 65536), (9000, True)]:
            with self.assertRaises(InvalidInput):
                NginxProxy(self.engine.directory, backend, port)

    def test_explicit_busy_frontend_never_starts_or_creates_runtime(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            with patch("local_osint.nginx.nginx_binary", return_value="/not-executed"), patch("local_osint.nginx.subprocess.Popen") as process:
                proxy = NginxProxy(self.engine.directory, 9000, occupied.getsockname()[1])
                with self.assertRaises(OSError):
                    proxy.start()
            process.assert_not_called()
            self.assertEqual(list(self.engine.directory.glob("nginx-*")), [])

    @unittest.skipUnless(NGINX, "Install nginx to run native proxy verification")
    def test_native_proxy_preserves_auth_host_origin_body_limit_and_private_evidence(self):
        with LocalServer(self.engine) as backend, NginxProxy(self.engine.directory, backend.server_port) as proxy:
            backend.host = f"127.0.0.1:{proxy.port}"
            backend.origin = proxy.origin
            worker = threading.Thread(target=backend.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
            worker.start()
            try:
                runtime = proxy.runtime
                child = proxy.process
                self.assertEqual(runtime.parent, self.engine.directory)
                self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
                self.assertEqual((runtime / "nginx.conf").stat().st_mode & 0o777, 0o600)

                def request(method, path, payload=None, headers=None):
                    connection = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
                    try:
                        connection.request(method, path, payload, headers or {})
                        response = connection.getresponse()
                        return response.status, response.getheader("Set-Cookie"), response.read()
                    finally:
                        connection.close()

                self.assertEqual(request("GET", "/")[0], 200)
                self.assertEqual(request("GET", "/api/status")[0], 401)
                self.assertEqual(request("GET", "/", headers={"Host": "evil.example"})[0], 403)
                self.assertEqual(request("GET", "/", headers={"Origin": "https://evil.example"})[0], 403)
                status, cookie, _ = request("POST", "/api/session", json.dumps({"token": backend.token}), {"Content-Type": "application/json", "Origin": proxy.origin, "X-OSINT-Local": "1"})
                self.assertEqual(status, 200)
                headers = {"Content-Type": "application/json", "Origin": proxy.origin, "X-OSINT-Local": "1", "Cookie": cookie.split(";", 1)[0]}
                status, _, body = request("GET", "/api/status", headers=headers)
                self.assertEqual(status, 200)
                self.assertFalse(json.loads(body)["network"])
                status, _, body = request("POST", "/api/cases", json.dumps({"name": "Proxy subject", "focus_type": "organization"}), headers)
                self.assertEqual(status, 201)
                self.assertEqual(json.loads(body)["name"], "Proxy subject")
                # A blocked cleanup must remain a pollable operation through
                # the real proxy, rather than occupying its HTTP timeout.
                entered, release = threading.Event(), threading.Event()
                stop = backend.search_service._cleanup_owned
                def slow_stop():
                    entered.set()
                    release.wait(15)
                    return stop()
                try:
                    with patch.object(backend.search_service, '_cleanup_owned', side_effect=slow_stop):
                        start = time.monotonic()
                        status, _, body = request('POST', '/api/search-service/stop', '{}', headers)
                        self.assertEqual(status, 202)
                        self.assertLess(time.monotonic() - start, 2)
                        self.assertEqual(json.loads(body)['state'], 'stopping')
                        self.assertTrue(entered.wait(1))
                        status, _, body = request('GET', '/api/search-service', headers=headers)
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)['state'], 'stopping')
                        release.set()
                        backend.search_service.stop_worker.join(2)
                        self.assertFalse(backend.search_service.stop_worker.is_alive())
                finally:
                    release.set()
                self.assertEqual(request("POST", "/api/cases", b"x" * (MAX_REQUEST + 1), headers)[0], 413)
                self.assertFalse(list(runtime.glob("*access*")))
                self.assertEqual(list((runtime / "body").iterdir()), [])
            finally:
                backend.shutdown()
                worker.join(timeout=3)
        self.assertIsNotNone(child.poll())
        if hasattr(os, 'killpg'):
            deadline = time.monotonic() + 3
            while True:
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    self.fail('App-owned nginx worker group survived proxy teardown.')
                time.sleep(0.02)
        self.assertFalse(runtime.exists())
        self.assertEqual(self.engine.cases()[0]["name"], "Proxy subject")

    @unittest.skipUnless(NGINX, "Install nginx to run native launcher verification")
    def test_launcher_sigterm_removes_owned_nginx_runtime_and_keeps_database(self):
        logfile = Path(self.temp.name) / "launch.log"
        with logfile.open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "local_osint", "serve", "--nginx", "--port", "0", "--data-dir", str(self.engine.directory)], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                output = logfile.read_text()
                if "Local OSINT ready." in output:
                    break
                if process.poll() is not None:
                    self.fail("Launcher exited before becoming ready.")
                time.sleep(0.02)
            self.assertIn("App-owned nginx server", output)
            port = int(re.search(r"http://127\.0\.0\.1:(\d+)/", output).group(1))
            runtime, = self.engine.directory.glob("nginx-*")
            child_pid = int((runtime / "nginx.pid").read_text())
            backend_port = int(re.search(r"proxy_pass http://127\.0\.0\.1:(\d+)", (runtime / "nginx.conf").read_text()).group(1))
            process.terminate()
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertFalse(runtime.exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
            for owned_port in [port, backend_port]:
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", owned_port), timeout=0.2)
            self.assertTrue(self.engine.path.exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

    @unittest.skipUnless(NGINX, "Install nginx to run native page-close verification")
    def test_last_page_close_tears_down_proxy_backend_and_launcher(self):
        logfile = Path(self.temp.name) / "page-close.log"
        with logfile.open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "local_osint", "serve", "--nginx", "--port", "0", "--data-dir", str(self.engine.directory)], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            output = ""
            while time.monotonic() < deadline and "Local OSINT ready." not in output:
                if process.poll() is not None:
                    self.fail("Launcher exited before becoming ready.")
                time.sleep(0.02)
                output = logfile.read_text()
            link = re.search(r"http://127\.0\.0\.1:(\d+)/#token=([^\s]+)", output)
            self.assertIsNotNone(link)
            port, token = int(link.group(1)), link.group(2)
            runtime, = self.engine.directory.glob("nginx-*")
            child_pid = int((runtime / "nginx.pid").read_text())
            backend_port = int(re.search(r"proxy_pass http://127\.0\.0\.1:(\d+)", (runtime / "nginx.conf").read_text()).group(1))
            headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", "X-OSINT-Local": "1"}
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("POST", "/api/session", json.dumps({"token": token}), headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                headers["Cookie"] = response.getheader("Set-Cookie").split(";", 1)[0]
                response.read()
                for path in ["/api/page/open", "/api/page/close"]:
                    connection.request("POST", path, json.dumps({"id": "11111111-2222-3333-4444-555555555555"}), headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
            finally:
                connection.close()
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertFalse(runtime.exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
            for owned_port in [port, backend_port]:
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", owned_port), timeout=0.2)
            self.assertTrue(self.engine.path.exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
