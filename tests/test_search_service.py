import base64
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import tarfile
import threading
import time
import tomllib
import unittest
from unittest.mock import patch

from local_osint.engine import InvalidInput
from local_osint.search_service import DEFAULT_IMAGE, HTTP_CLIENT, LABEL_NONCE, LABEL_WORKSPACE, SearchService


class SearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        with patch("local_osint.search_service.shutil.which", return_value="/fixture/docker"):
            self.manager = SearchService(Path(self.tmp.name))
        self.calls, self.running, self.created = [], False, False
        self.image_id = "sha256:" + "a" * 64

    def tearDown(self):
        try:
            with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
                self.manager.close()
        finally:
            self.tmp.cleanup()

    def fake_runtime(self, args, *, input=None, timeout=15, cancellable=True):
        self.calls.append((args, input))
        if args[:2] == ["image", "inspect"]:
            return json.dumps([{"Id": self.image_id}]).encode()
        if args[:2] == ["container", "inspect"]:
            if not self.created:
                raise InvalidInput("Missing fixture container")
            return json.dumps([{"Config": {"Labels": {LABEL_WORKSPACE: self.manager.workspace, LABEL_NONCE: self.manager.state["nonce"]}}, "State": {"Running": self.running}, "HostConfig": {"PortBindings": {}}}]).encode()
        if args[:2] == ["container", "ls"]:
            return (self.manager.state["container"] if self.created else "").encode()
        if args[0] == "create":
            self.created = True
        if args[0] == "start":
            self.running = True
        if args[0] == "stop":
            self.running = False
        if args[0] == "rm":
            self.created = False
        if args[0] == "exec":
            request = json.loads(input)
            if request["path"] == "/config":
                payload = b'{"engines": []}'
            else:
                payload = b'{"results": [], "unresponsive_engines": [["bing", "timeout"]]}'
            return json.dumps({"status": 200, "body": base64.b64encode(payload).decode()}).encode()
        return b"ok"

    def test_missing_runtime_actionable_and_no_implicit_setup(self):
        with patch("local_osint.search_service.shutil.which", return_value=None):
            manager = SearchService(Path(self.tmp.name) / "missing")
        self.addCleanup(manager.close)
        self.assertEqual(manager.status()["state"], "unavailable")
        with self.assertRaises(InvalidInput):
            manager.start()
        with patch.object(manager, "_run") as run:
            with self.assertRaises(InvalidInput):
                manager.search("Public topic")
            run.assert_not_called()

    def test_setup_pins_image_then_start_never_pulls_or_publishes_port(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            prepared = self.manager.setup({"engines": ["bing", "duckduckgo"]})
            self.assertEqual(prepared["image_id"], self.image_id)
            self.assertEqual(prepared["state"], "stopped")
            pull_count = sum(args[0] == "pull" for args, _ in self.calls)
            ready = self.manager.start()
            self.assertEqual(ready["state"], "ready")
            self.assertFalse(ready["published_ports"])
            self.assertEqual(sum(args[0] == "pull" for args, _ in self.calls), pull_count)
            create = next(args for args, _ in self.calls if args[0] == "create")
            self.assertIn(self.image_id, create)
            self.assertIn("--pull=never", create)
            self.assertNotIn("-p", create)
            self.assertNotIn("--publish", create)
            self.assertNotIn("--volume", create)
            self.assertEqual(create[create.index("--user") + 1], "977:977")
            payload = next(body for args, body in self.calls if args[0] == "cp")
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                config = json.load(archive.extractfile("settings.yml"))
                limiter = tomllib.loads(archive.extractfile("limiter.toml").read().decode())
                self.assertEqual(limiter['botdetection']['trusted_proxies'], ['127.0.0.1/32'])
                self.assertTrue(all((file.mode, file.uid, file.gid) == (0o600, 977, 977) for file in archive.getmembers()))
            self.assertEqual(config["search"]["formats"], ["json"])
            self.assertEqual(config["use_default_settings"]["engines"]["keep_only"], ["bing", "duckduckgo"])
            self.manager.stop()
            self.assertFalse(self.created)

    def test_setup_rejects_commands_foreign_images_and_unreviewed_engines_before_runtime(self):
        with patch.object(self.manager, "_run") as run:
            for settings in ({"image": "evil/image:latest"}, {"image": DEFAULT_IMAGE + ";id"}, {"engines": ["google"]}, {"engines": ["bing", "bing"]}, {"command": "shell"}, {"endpoint": "http://localhost"}):
                with self.subTest(settings=settings), self.assertRaises(InvalidInput):
                    self.manager.setup(settings)
            run.assert_not_called()

    def test_search_preserves_raw_json_and_explicit_fanout_failures(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup({"engines": ["bing", "brave"]})
            self.manager.start()
            result = self.manager.search("Public topic")
            self.assertEqual(result["upstream_fanout"], 2)
            self.assertEqual(result["upstream_failures"], [["bing", "timeout"]])
            self.assertEqual(json.loads(result["raw"])["results"], [])
            request = next(json.loads(body) for args, body in reversed(self.calls) if args[0] == "exec")
            self.assertEqual(request["path"], "/search")
            self.assertIn("engines=bing%2Cbrave", request["body"])

    def test_owned_cleanup_refuses_foreign_labels_and_retains_receipt(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup()
            self.manager.start()
        original = self.manager.state.copy()
        with patch.object(self.manager, "_run", return_value=json.dumps([{"Config": {"Labels": {LABEL_WORKSPACE: "other"}}, "State": {"Running": True}}]).encode()) as run:
            with self.assertRaises(InvalidInput):
                self.manager.stop()
            self.assertFalse(any(call.args[0][0] in {"stop", "rm"} for call in run.call_args_list))
        self.assertEqual(self.manager.state, original)

    def test_oversized_search_response_is_rejected(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup()
            self.manager.start()
        with patch.object(self.manager, "_inspect_owned", return_value={"State": {"Running": True}}), patch.object(self.manager, "_run", return_value=json.dumps({"status": 200, "body": base64.b64encode(b"x" * 2097153).decode()}).encode()):
            with self.assertRaises(InvalidInput):
                self.manager.search("Public topic")

    def test_async_start_reports_progress_without_blocking_then_becomes_ready(self):
        entered, release = threading.Event(), threading.Event()
        def runtime(args, **kwargs):
            if args[0] == "pull":
                entered.set()
                release.wait(timeout=2)
            return self.fake_runtime(args, **kwargs)
        with patch.object(self.manager, "_run", side_effect=runtime):
            result = self.manager.start_async({"engines": ["bing"]})
            self.assertEqual(result["state"], "preparing")
            self.assertTrue(entered.wait(timeout=1))
            began = time.monotonic()
            self.assertEqual(self.manager.status()["state"], "setting_up")
            self.assertLess(time.monotonic() - began, 0.5)
            release.set()
            self.manager.worker.join(timeout=2)
            self.assertFalse(self.manager.worker.is_alive())
            self.assertEqual(self.manager.status()["state"], "ready")
            self.manager.stop()

    def test_docker_failure_retains_owned_container_receipt_for_later_cleanup(self):
        self.manager.state = {"image_id": self.image_id, "engines": ["bing"],
                              "container": "osint-search-" + self.manager.workspace + "-" + "b" * 16, "nonce": "b" * 16}
        original = self.manager.state.copy()
        with patch.object(self.manager, "_run", side_effect=InvalidInput("Container operation failed.")):
            self.manager.start_async({})
            self.manager.worker.join(timeout=2)
            result = self.manager.status()
        self.assertEqual(result["state"], "error")
        self.assertTrue(result["owned_container"])
        self.assertIn("Start Docker", result["error"])
        self.assertEqual(self.manager.state, original)

    def test_async_failure_is_visible_and_does_not_claim_readiness(self):
        with patch.object(self.manager, "_run", side_effect=InvalidInput("Docker is not running.")):
            self.manager.start_async({})
            self.manager.worker.join(timeout=2)
            result = self.manager.status()
        self.assertNotEqual(result["state"], "ready")
        self.assertIn("Docker", result["error"])

    def test_running_container_without_http_service_is_not_ready(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup()
            self.manager.start()
            def unavailable(args, **kwargs):
                if args[0] == "exec":
                    return json.dumps({"status": 503, "body": base64.b64encode(b"not ready").decode()}).encode()
                return self.fake_runtime(args, **kwargs)
            with patch.object(self.manager, "_run", side_effect=unavailable):
                state = self.manager.status()
                with self.assertRaises(InvalidInput):
                    self.manager.start()
            self.assertNotEqual(state["state"], "ready")
            self.assertTrue(state["owned_container"])
            self.assertIn("Stop", state["message"])
            with patch.object(self.manager, "_request", side_effect=InvalidInput("Gateway unavailable")):
                self.assertNotEqual(self.manager.status()["state"], "ready")
                with self.assertRaises(InvalidInput):
                    self.manager.start()
            self.assertEqual(self.manager.status()["state"], "ready")
            self.manager.stop()

    def test_existing_stopped_container_cannot_shortcut_startup_as_ready(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup()
            self.manager.start()
            self.running = False
            with self.assertRaisesRegex(InvalidInput, "Stop"):
                self.manager.start()
            self.manager.stop()

    def test_command_errors_identify_the_failed_stage_without_exposing_stderr(self):
        executable = Path(self.tmp.name) / 'failed-runtime'
        executable.write_text('#!' + sys.executable + '\nimport sys\nprint("private runtime detail", file=sys.stderr)\nsys.exit(1)\n')
        executable.chmod(0o700)
        self.manager.binary = str(executable)
        for command, stage in [('pull', 'image download'), ('create', 'container creation'), ('cp', 'configuration copy')]:
            with self.subTest(command=command), self.assertRaises(InvalidInput) as failure:
                self.manager._run([command])
            self.assertIn(stage, str(failure.exception))
            self.assertNotIn('private runtime detail', str(failure.exception))

    def test_stopped_docker_fails_before_setup_and_can_retry_without_reopening(self):
        original = {"image_id": self.image_id, "engines": ["bing"]}
        self.manager.state = original.copy()
        for settings in ({}, None):
            with self.subTest(settings=settings), patch.object(self.manager, "_run", side_effect=InvalidInput("Container operation failed.")) as run:
                self.manager.start_async(settings)
                self.manager.worker.join(timeout=2)
                result = self.manager.status()
            self.assertEqual(result["state"], "error")
            self.assertIn("Start Docker", result["error"])
            self.assertIn("retry", result["error"].lower())
            self.assertEqual(self.manager.state, original)
            self.assertEqual([call.args[0][0] for call in run.call_args_list], ["info"])
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.start_async({})
            self.manager.worker.join(timeout=2)
            self.assertEqual(self.manager.status()["state"], "ready")
            self.assertEqual(self.manager.status()["error"], "")
            self.manager.stop()

    def test_stop_cancels_delayed_setup_and_suppresses_late_start(self):
        entered = threading.Event()
        def runtime(args, **kwargs):
            if args[0] == "pull":
                entered.set()
                self.manager.cancel.wait(timeout=2)
            return self.fake_runtime(args, **kwargs)
        with patch.object(self.manager, "_run", side_effect=runtime):
            self.manager.start_async({})
            self.assertTrue(entered.wait(timeout=1))
            result = self.manager.stop()
            self.assertEqual(result["state"], "stopped")
            self.assertFalse(self.manager.worker.is_alive())
        self.assertFalse(any(args[0] in {"create", "start"} for args, _ in self.calls))

    def test_new_launch_reconciles_only_persisted_owned_container(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup()
            self.manager.start()
        # Simulate OS releasing a crashed owner's descriptor without cleanup.
        self.manager.lease.release()
        self.manager.closed = True
        with patch("local_osint.search_service.shutil.which", return_value="/fixture/docker"), patch.object(SearchService, "_run", side_effect=self.fake_runtime):
            recovered = SearchService(Path(self.tmp.name))
        self.assertFalse(self.created)
        self.assertNotIn("container", recovered.state)
        self.assertEqual(recovered.state["image_id"], self.image_id)
        recovered.close()

    def test_second_live_manager_cannot_touch_first_receipt_even_after_stop(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup()
            self.manager.start()
            original = self.manager.state.copy()
            with patch.object(SearchService, "_run") as run:
                with self.assertRaisesRegex(InvalidInput, "already open"):
                    SearchService(Path(self.tmp.name))
                run.assert_not_called()
            self.assertEqual(self.manager.state, original)
            self.manager.stop()
            with self.assertRaisesRegex(InvalidInput, "already open"):
                SearchService(Path(self.tmp.name))
            self.manager.close()
        with patch("local_osint.search_service.shutil.which", return_value=None):
            replacement = SearchService(Path(self.tmp.name))
        replacement.close()

    def test_failed_initialization_releases_manager_lease(self):
        directory = Path(self.tmp.name) / "invalid"
        with patch.object(SearchService, "_load", side_effect=InvalidInput("Invalid state")):
            with self.assertRaises(InvalidInput):
                SearchService(directory)
        replacement = SearchService(directory)
        replacement.close()

    def test_process_exit_releases_search_manager_ownership(self):
        directory = Path(self.tmp.name) / "child-owner"
        code = "from local_osint.search_service import SearchService; import sys,time; manager=SearchService(sys.argv[1]); print('ready',flush=True); time.sleep(30)"
        with subprocess.Popen([sys.executable, "-c", code, str(directory)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as child:
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                with self.assertRaisesRegex(InvalidInput, "already open"):
                    SearchService(directory)
                child.kill()
                child.wait(timeout=5)
                recovered = SearchService(directory)
                recovered.close()
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)

    def test_close_failure_preserves_receipt_but_releases_finished_manager(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup()
            self.manager.start()
        original = self.manager.state.copy()
        with patch.object(self.manager, "_run", side_effect=InvalidInput("Docker unavailable")):
            with self.assertRaises(InvalidInput):
                self.manager.close()
        self.assertEqual(self.manager.state, original)
        self.assertFalse(self.manager.lease.held)
        self.assertFalse(self.manager.stop_worker.is_alive())
        with patch("local_osint.search_service.shutil.which", return_value="/fixture/docker"), patch.object(SearchService, "_run", side_effect=self.fake_runtime):
            recovered = SearchService(Path(self.tmp.name))
            recovered.close()
        self.assertFalse(self.created)

    def test_unexpected_background_failure_has_private_diagnostic_reference(self):
        from local_osint.diagnostics import Diagnostics
        recorder = Diagnostics(Path(self.tmp.name))
        self.manager.diagnostics = recorder
        for operation in ("start", "stop"):
            with self.subTest(operation=operation), patch.object(self.manager, "_run", side_effect=RuntimeError("private query and token")):
                if operation == "start":
                    self.manager.start_async({})
                    worker = self.manager.worker
                else:
                    self.manager.state["container"] = "osint-search-" + self.manager.workspace + "-" + "b" * 16
                    self.manager.state["nonce"] = "b" * 16
                    self.manager.stop_async()
                    worker = self.manager.stop_worker
                worker.join(timeout=2)
                # Status also probes the runtime; use only the terminal error
                # snapshot to avoid introducing a separate foreground failure.
                self.assertIn(recorder.recent()[0]["id"], self.manager.last_error)
                self.assertEqual(recorder.recent()[0]["operation"], "search_service_" + operation)
                self.assertNotIn("private query", self.manager.last_error)
                self.assertNotIn("private query", recorder.path.read_text())
        self.manager.state.pop("container")
        self.manager.state.pop("nonce")

    def test_closed_manager_rejects_mutations_and_close_is_idempotent(self):
        self.manager.close()
        with patch.object(self.manager, "_run") as run:
            for operation in (self.manager.start, self.manager.start_async, self.manager.setup,
                              self.manager.stop, self.manager.stop_async,
                              lambda: self.manager.search("public topic")):
                with self.subTest(operation=operation), self.assertRaisesRegex(InvalidInput, "closed"):
                    operation()
            self.manager.close()
            run.assert_not_called()
        self.assertEqual(self.manager.status()["state"], "closed")

    def test_async_stop_is_prompt_pollable_and_repeated_stop_joins_same_work(self):
        entered, release = threading.Event(), threading.Event()
        def runtime(args, **kwargs):
            if args[0] == "stop":
                entered.set()
                release.wait(timeout=3)
            return self.fake_runtime(args, **kwargs)
        with patch.object(self.manager, "_run", side_effect=runtime):
            self.manager.setup()
            self.manager.start()
            began = time.monotonic()
            self.assertEqual(self.manager.stop_async()["state"], "stopping")
            self.assertLess(time.monotonic() - began, 0.5)
            self.assertTrue(entered.wait(timeout=1))
            worker = self.manager.stop_worker
            self.assertEqual(self.manager.status()["state"], "stopping")
            self.assertEqual(self.manager.stop_async()["state"], "stopping")
            self.assertIs(self.manager.stop_worker, worker)
            with self.assertRaises(InvalidInput):
                self.manager.start_async()
            release.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.manager.status()["state"], "stopped")
            self.assertEqual(sum(args[0] == "stop" for args, _ in self.calls), 1)

    def test_async_stop_failure_retains_receipt_and_retry_cleans_it(self):
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.setup()
            self.manager.start()
        original = self.manager.state.copy()
        def failing(args, **kwargs):
            if args[0] == "stop":
                raise InvalidInput("Docker stop failed. Start Docker and retry Stop.")
            return self.fake_runtime(args, **kwargs)
        with patch.object(self.manager, "_run", side_effect=failing):
            self.manager.stop_async()
            self.manager.stop_worker.join(timeout=2)
            result = self.manager.status()
            self.assertEqual(result["state"], "error")
            self.assertIn("retry Stop", result["error"])
            self.assertEqual(self.manager.state, original)
        with patch.object(self.manager, "_run", side_effect=self.fake_runtime):
            self.manager.stop_async()
            self.manager.stop_worker.join(timeout=2)
            self.assertEqual(self.manager.status()["state"], "stopped")
            self.assertFalse(self.created)

    def test_async_stop_cancels_original_start_worker_before_cleanup(self):
        entered = threading.Event()
        def runtime(args, **kwargs):
            if args[0] == "pull":
                entered.set()
                self.manager.cancel.wait(timeout=2)
            return self.fake_runtime(args, **kwargs)
        with patch.object(self.manager, "_run", side_effect=runtime):
            self.manager.start_async({})
            startup = self.manager.worker
            self.assertTrue(entered.wait(timeout=1))
            self.manager.stop_async()
            self.manager.close()
            self.assertFalse(startup.is_alive())
            self.assertFalse(self.manager.stop_worker.is_alive())
            self.assertFalse(self.manager.lease.held)
        self.assertFalse(any(args[0] in {"create", "start"} for args, _ in self.calls))

    def test_stop_terminates_owned_container_cli_and_joins_setup_worker(self):
        marker = Path(self.tmp.name) / "pull.pid"
        fixture = Path(self.tmp.name) / "fake-docker"
        fixture.write_text(f'#!{sys.executable}\nimport os,pathlib,sys,time\nif sys.argv[1] == "pull":\n pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n time.sleep(30)\nelse:\n print("fixture")\n')
        fixture.chmod(0o700)
        self.manager.binary = str(fixture)
        self.manager.start_async({})
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        pid = int(marker.read_text())
        began = time.monotonic()
        self.manager.stop()
        self.assertLess(time.monotonic() - began, 3)
        self.assertFalse(self.manager.worker.is_alive())
        self.assertIsNone(self.manager.command_process)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_http_gateway_uses_fixed_loopback_without_proxy_or_redirect_handling(self):
        # Execute the actual fixed gateway with an in-memory HTTP transport.
        calls = []
        class Response:
            status = 200
            def read(self, limit):
                calls.append(("read", limit))
                return b'{"results":[]}'
        class Connection:
            def __init__(self, host, port, timeout):
                calls.append(("connect", host, port, timeout))
            def request(self, method, path, body=None, headers=None):
                calls.append((method, path, headers))
            def getresponse(self):
                return Response()
            def close(self):
                pass
        output = io.StringIO()
        with patch("http.client.HTTPConnection", Connection), patch("sys.stdin", io.StringIO('{"method":"POST","path":"/search","body":"q=public"}')), patch("sys.stdout", output):
            exec(HTTP_CLIENT, {})
        self.assertEqual(calls[0][1:3], ("127.0.0.1", 8080))
        request = next(call for call in calls if call[0] == 'POST')
        self.assertEqual(request[2]['X-Forwarded-For'], '127.0.0.1')
        self.assertEqual(request[2]['X-Real-IP'], '127.0.0.1')
        self.assertEqual(json.loads(output.getvalue())["status"], 200)


if __name__ == "__main__":
    unittest.main()
