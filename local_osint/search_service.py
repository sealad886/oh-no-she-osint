"""Optional owned SearXNG container. No host listener or arbitrary service URL.

Setup is explicit and may download the official image. Investigations only use
an already-running container. HTTP stays on container loopback; the private
container-runtime channel is the gateway, avoiding an unauthenticated host port.
SearXNG upstream transport is separate from the pinned public-page collector.
"""

import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time

from .collector import public_url
from .engine import InvalidInput, MAX_BYTES, private_directory
from .owned_process import WorkerLease
from .private_files import write_private_json as _write_private


DEFAULT_IMAGE = "docker.io/searxng/searxng:latest"
ENGINES = ("bing", "duckduckgo", "brave")
LABEL_WORKSPACE = "org.local-osint.workspace"
LABEL_NONCE = "org.local-osint.launch"
PYTHON = "/usr/local/searxng/.venv/bin/python"
HTTP_CLIENT = """import base64,http.client,json,sys
d=json.load(sys.stdin)
c=http.client.HTTPConnection('127.0.0.1',8080,timeout=d.get('timeout',25))
try:
 c.request(d['method'],d['path'],body=d.get('body'),headers={'Content-Type':'application/x-www-form-urlencoded','Accept-Encoding':'identity','X-Forwarded-For':'127.0.0.1','X-Real-IP':'127.0.0.1'})
 r=c.getresponse(); b=r.read(2097153)
 if len(b)>2097152: raise ValueError('Response exceeded limit')
 print(json.dumps({'status':r.status,'body':base64.b64encode(b).decode('ascii')}))
finally: c.close()
"""


class SearchService:
    def __init__(self, directory, diagnostics=None):
        self.directory = private_directory(Path(directory) / "search-service")
        self.lease = WorkerLease(self.directory, "manager.lock")
        self.path = self.directory / "state.json"
        self.diagnostics = diagnostics
        self.workspace = hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:24]
        self.binary = shutil.which("docker")
        self.lock = threading.RLock()
        self.worker_lock = threading.RLock()
        self.process_lock = threading.RLock()
        self.close_lock = threading.RLock()
        self.worker = None
        self.stop_worker = None
        self.closed = self.closing = False
        self.command_process = None
        self.cancel = threading.Event()
        self.stopping = False
        self.last_error = ""
        self.phase = ""
        self.message = "Set up the optional search service. Docker must already be installed and running."
        if not self.lease.acquire():
            raise InvalidInput("This workspace is already open in another app session. Close that session before reopening it.")
        try:
            self.state = self._load()
            # Lease proves no live manager owns this receipt before reconciliation.
            if self.binary and self.state.get("container"):
                try:
                    self.stop()
                except InvalidInput:
                    self.message = "Previous search service ownership retained. Start Docker, then stop/reconcile the owned service."
        except BaseException:
            self.closed = True
            self.lease.release()
            raise

    def _ensure_open(self):
        if self.closed or self.closing:
            raise InvalidInput("This search service session is closed. Relaunch the app to start a new session.")

    def _unexpected_error(self, operation, exc):
        message = "Search service operation failed. Retry, or reopen the app if the problem persists."
        if self.diagnostics:
            event = self.diagnostics.record(operation, exc)
            message += " Diagnostic reference: " + event["id"] + "."
        self.last_error = self.message = message

    def _load(self):
        if self.path.is_symlink():
            raise InvalidInput("Search service state must not be a symlink.")
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > 16384:
            raise InvalidInput("Search service state exceeds its size limit.")
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise InvalidInput("Search service state is unreadable; retain it for recovery.") from exc
        if not isinstance(data, dict):
            raise InvalidInput("Search service state is invalid.")
        return data

    def _run(self, arguments, *, input=None, timeout=15, cancellable=True):
        if not self.binary:
            raise InvalidInput("Docker is unavailable. Install and start Docker separately, then reopen the app.")
        process = None
        operation = {"info": "Docker connection check", "pull": "image download", "image": "image inspection",
                     "create": "container creation", "cp": "configuration copy", "start": "container startup",
                     "exec": "private HTTP request", "container": "container inspection", "stop": "container stop",
                     "rm": "container cleanup"}.get(arguments[0], "container operation")
        try:
            process = subprocess.Popen([self.binary, *arguments], stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            with self.process_lock:
                self.command_process = process
            deadline, first = time.monotonic() + timeout, True
            while True:
                if cancellable:
                    self._check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                try:
                    stdout, _ = process.communicate(input=input if first else None, timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    first = False
            if process.returncode:
                raise InvalidInput(f"Search service {operation} failed. Check Docker availability, image compatibility and free disk space.")
            return stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            outcome = "timed out" if isinstance(exc, subprocess.TimeoutExpired) else "could not run"
            raise InvalidInput(f"Search service {operation} {outcome}. Check Docker connectivity; retry setup if the image download was interrupted.") from exc
        finally:
            if process is not None and process.poll() is None:
                self._terminate_command(process)
            with self.process_lock:
                if self.command_process is process:
                    self.command_process = None

    @staticmethod
    def _terminate_command(process):
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=2)

    def _check_cancelled(self):
        if threading.current_thread() is self.worker and self.cancel.is_set():
            raise InvalidInput("Search service setup/start was cancelled.")

    def _check_docker(self):
        if not self.binary:
            raise InvalidInput("Docker is unavailable. Install and start Docker separately, then reopen the app.")
        try:
            self._run(["info", "--format", "{{.ServerVersion}}"], timeout=5)
        except InvalidInput as exc:
            self._check_cancelled()
            raise InvalidInput("Cannot connect to Docker. Start Docker Desktop or your Docker engine, wait until it is ready, then retry Set up & start metasearch. If Docker is already running, check its connection and permissions.") from exc

    def _inspect_owned(self, cancellable=True, timeout=5):
        container, nonce = self.state.get("container"), self.state.get("nonce")
        if not isinstance(container, str) or not re.fullmatch(r"osint-search-[a-f0-9]{24}-[a-f0-9]{16}", container) or not isinstance(nonce, str):
            return None
        try:
            records = json.loads(self._run(["container", "inspect", container], timeout=timeout, cancellable=cancellable))
        except (InvalidInput, ValueError, TypeError):
            return None
        if not isinstance(records, list) or len(records) != 1:
            return None
        record = records[0]
        labels = record.get("Config", {}).get("Labels", {})
        if labels.get(LABEL_WORKSPACE) != self.workspace or labels.get(LABEL_NONCE) != nonce:
            return None
        if record.get("HostConfig", {}).get("PortBindings") or record.get("HostConfig", {}).get("NetworkMode") == "host":
            raise InvalidInput("Owned search service has unexpected host exposure; stop it manually and check its configuration.")
        return record

    def status(self):
        if self.closed:
            return self._snapshot("closed")
        if self.stopping:
            return self._snapshot("stopping")
        if self.worker and self.worker.is_alive() and self.phase:
            return self._snapshot(self.phase)
        if not self.lock.acquire(blocking=False):
            return self._snapshot(self.phase or "busy")
        try:
            record = self._inspect_owned(timeout=2) if self.state.get("container") else None
            ready = bool(record and record.get("State", {}).get("Running") and self._healthy())
            if record and not ready and not self.last_error:
                return self._snapshot("unhealthy")
            return self._snapshot("error" if self.last_error else "ready" if ready else "stopped" if self.state.get("image_id") else "unavailable" if not self.binary else "not_configured")
        finally:
            self.lock.release()

    def _healthy(self):
        """Probe already-verified ownership without repeating container inspection."""
        try:
            return self._request("GET", "/healthz", timeout=2)["status"] == 200
        except InvalidInput:
            return False

    def _snapshot(self, state):
        messages = {"setting_up": "Preparing and pinning the official search image. This may take several minutes.",
                    "preparing": "Preparing the owned local search service in the background.",
                    "starting": "Starting the owned local search service.", "stopping": "Stopping the owned local search service.",
                    "ready": "Owned search service ready; HTTP is private to the container.",
                    "unhealthy": "SearXNG is not responding. Use Stop metasearch, then Set up & start metasearch to retry."}
        return {"state": state, "message": messages.get(state, self.message), "error": self.last_error,
                "configured": bool(self.state.get("image_id")), "image": self.state.get("image", DEFAULT_IMAGE),
                "owned_container": bool(self.state.get("container")),
                "image_id": self.state.get("image_id", ""), "engines": self.state.get("engines", list(ENGINES)),
                "upstream_fanout": len(self.state.get("engines", ENGINES)), "published_ports": False,
                "transport": "container-private loopback via Docker exec", "license": "AGPL-3.0-or-later"}

    @contextmanager
    def _operation(self, phase, cleanup=False):
        with self.lock:
            if not cleanup:
                self._ensure_open()
                self._check_cancelled()
                if self.stopping:
                    raise InvalidInput("The search service is stopping. Wait for completion before retrying.")
            previous = self.phase
            self.phase = phase
            try:
                yield
            finally:
                self.phase = previous

    def setup(self, settings=None):
        """Explicit app action: download an official image, then pin immutable ID."""
        settings = settings or {}
        if not isinstance(settings, dict) or set(settings) - {"image", "engines"}:
            raise InvalidInput("Search setup accepts only the official image and selected engines.")
        image = settings.get("image") or DEFAULT_IMAGE
        if not isinstance(image, str) or not re.fullmatch(r"docker\.io/searxng/searxng(?::[A-Za-z0-9_.-]{1,100}|@sha256:[a-f0-9]{64})", image):
            raise InvalidInput("Use an official docker.io/searxng/searxng tag or sha256 digest.")
        engines = settings.get("engines", list(ENGINES))
        if not isinstance(engines, list) or not engines or len(engines) > len(ENGINES) or any(item not in ENGINES for item in engines) or len(set(engines)) != len(engines):
            raise InvalidInput("Choose one or more of Bing, DuckDuckGo and Brave without duplicates.")
        with self._operation("setting_up"):
            self._check_docker()
            if self.state.get("container"):
                raise InvalidInput("Stop the owned search service before changing its setup.")
            self._run(["pull", image], timeout=180)
            self._check_cancelled()
            records = json.loads(self._run(["image", "inspect", image], timeout=10))
            identity = records[0].get("Id", "")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
                raise InvalidInput("Docker did not return an immutable image identity.")
            self.state = {"image": image, "image_id": identity, "engines": engines}
            _write_private(self.path, self.state)
            self.message = "Image prepared and pinned. Start the search service to use it."
            return self.status()

    def start_async(self, settings=None):
        """Start explicit setup/start off the HTTP request thread; status is pollable."""
        if settings is not None and not isinstance(settings, dict):
            raise InvalidInput("Search setup settings must be an object.")
        with self.worker_lock:
            self._ensure_open()
            if self.stopping or (self.worker and self.worker.is_alive()):
                raise InvalidInput("A search service operation is already running.")
            self.cancel.clear()
            self.phase, self.last_error = "preparing", ""
            def run():
                try:
                    self._check_cancelled()
                    self.start(settings)
                except InvalidInput as exc:
                    self.last_error = str(exc)
                    self.message = self.last_error
                except Exception as exc:
                    self._unexpected_error("search_service_start", exc)
                finally:
                    self.phase = ""
            self.worker = threading.Thread(target=run, name="owned-search-service", daemon=True)
            self.worker.start()
            return self._snapshot("preparing")

    def start(self, settings=None):
        with self._operation("starting"):
            if settings is not None:
                self.setup(settings)
            else:
                self._check_docker()
            self._check_cancelled()
            existing = self._inspect_owned()
            if existing:
                if not existing.get("State", {}).get("Running"):
                    raise InvalidInput("The owned search container is stopped. Use Stop metasearch, then Set up & start metasearch to retry.")
                if not self._healthy():
                    raise InvalidInput("SearXNG is not responding. Use Stop metasearch, then Set up & start metasearch to retry.")
                return self._snapshot("ready")
            if self.state.get("container"):
                raise InvalidInput("Previous search service ownership could not be verified. Start Docker and stop/reconcile it before restarting.")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", self.state.get("image_id", "")):
                raise InvalidInput("Set up the optional search service before starting it.")
            nonce = secrets.token_hex(8)
            name = "osint-search-" + self.workspace + "-" + nonce
            # Persist intent before create so a crash at any later point is recoverable.
            self.state.update(container=name, nonce=nonce)
            _write_private(self.path, self.state)
            config = {"use_default_settings": {"engines": {"keep_only": self.state["engines"]}},
                      "server": {"secret_key": secrets.token_hex(32), "limiter": False, "public_instance": False, "image_proxy": False, "bind_address": "127.0.0.1"},
                      "search": {"formats": ["json"], "default_lang": "en", "safe_search": 1},
                      "outgoing": {"request_timeout": 8.0, "max_request_timeout": 12.0, "retries": 0},
                      "engines": [{"name": item, "disabled": False} for item in self.state["engines"]]}
            try:
                self._run(["create", "--name", name, "--pull=never", "--label", LABEL_WORKSPACE + "=" + self.workspace,
                           "--label", LABEL_NONCE + "=" + nonce, "--memory", "768m", "--cpus", "1", "--pids-limit", "128",
                           "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--user", "977:977",
                           "--workdir", "/usr/local/searxng", "--env", "SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml",
                           "--entrypoint", "/usr/local/searxng/.venv/bin/granian", self.state["image_id"],
                           "--interface", "wsgi", "--host", "127.0.0.1", "--port", "8080", "searx.webapp:app"], timeout=15)
                # Copy through stdin, avoiding a world-readable host config or a host bind mount.
                self._run(["cp", "-", name + ":/etc/searxng"], input=self._configuration_archive(config), timeout=15)
                self._run(["start", name], timeout=15)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    self._check_cancelled()
                    try:
                        response = self._http("GET", "/config")
                        if response["status"] == 200:
                            self.message = "Owned search service ready."
                            return self.status()
                    except InvalidInput:
                        self._check_cancelled()
                        time.sleep(0.3)
                raise InvalidInput("Search service failed to become ready. Check Docker image compatibility.")
            except BaseException:
                self._cleanup_owned()
                raise

    @staticmethod
    def _configuration_archive(config):
        import io
        import tarfile
        files = {"settings.yml": json.dumps(config).encode(),  # JSON is a YAML subset accepted by SearXNG.
                 "limiter.toml": b'# Only the app gateway on container loopback supplies forwarding headers.\n[botdetection]\ntrusted_proxies = ["127.0.0.1/32"]\n'}
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.uid, member.gid = len(content), 0o600, 977, 977
                archive.addfile(member, io.BytesIO(content))
        return output.getvalue()

    def _http(self, method, path, body=None):
        if not self._inspect_owned():
            raise InvalidInput("The owned search service is unavailable. Start it in Sources.")
        return self._request(method, path, body)

    def _request(self, method, path, body=None, timeout=35):
        """Fixed gateway request after the caller verifies container ownership."""
        raw = self._run(["exec", "-i", self.state["container"], PYTHON, "-c", HTTP_CLIENT],
                        input=json.dumps({"method": method, "path": path, "body": body,
                                          "timeout": min(25, max(0.1, timeout - 1))}).encode(), timeout=timeout)
        try:
            data = json.loads(raw)
            payload = base64.b64decode(data["body"], validate=True)
            if len(payload) > MAX_BYTES:
                raise ValueError("oversize")
            return {"status": data["status"], "raw": payload}
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidInput("Owned search service returned an invalid or oversized response.") from exc

    def search(self, query):
        from urllib.parse import urlencode
        if not isinstance(query, str) or not query.strip() or len(query) > 300 or any(ord(c) < 32 for c in query):
            raise InvalidInput("Use a public search query up to 300 characters without control characters.")
        with self.lock:
            self._ensure_open()
            if self.stopping:
                raise InvalidInput("The search service is stopping. Wait for completion before searching.")
            if not self.state.get("container") or not self.state.get("engines"):
                raise InvalidInput("Start the configured local search service in Sources before searching.")
            response = self._http("POST", "/search", urlencode({"q": query, "format": "json", "engines": ",".join(self.state["engines"])}))
            if response["status"] != 200:
                return {**response, "upstream_fanout": len(self.state["engines"]), "upstream_failures": [], "uninterpretable": False}
            try:
                data = json.loads(response["raw"])
                if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                    raise ValueError("Invalid result shape")
            except (ValueError, TypeError) as exc:
                return {**response, "upstream_fanout": len(self.state["engines"]), "upstream_failures": [], "uninterpretable": True}
            failures = data.get("unresponsive_engines", [])
            return {**response, "upstream_fanout": len(self.state["engines"]),
                    "upstream_failures": failures if isinstance(failures, list) else [],
                    "request_count_note": "Configured engine fanout; exact upstream requests may differ. Separate transport from pinned HTTPS collector."}

    def _begin_stop(self):
        """Called under worker_lock; keep the startup identity until it is joined."""
        if self.stop_worker and self.stop_worker.is_alive():
            return self.stop_worker
        startup = self.worker
        self.stopping = True
        self.cancel.set()
        self.last_error = ""
        def run():
            try:
                with self.process_lock:
                    process = self.command_process
                if startup and startup.is_alive() and process and process.poll() is None:
                    process.terminate()
                if startup and startup is not threading.current_thread():
                    # CLI operations have bounded timeouts; retain the lease until
                    # the original worker and its cancellation cleanup are done.
                    startup.join()
                self._cleanup_owned()
            except InvalidInput as exc:
                self.last_error = self.message = str(exc)
            except Exception as exc:
                self._unexpected_error("search_service_stop", exc)
            finally:
                with self.worker_lock:
                    self.phase = ""
                    self.stopping = False
        self.stop_worker = threading.Thread(target=run, name="owned-search-service-stop", daemon=True)
        self.stop_worker.start()
        return self.stop_worker

    def stop_async(self):
        """Return immediately while status exposes stopping, stopped or error."""
        with self.worker_lock:
            self._ensure_open()
            self._begin_stop()
            return self._snapshot("stopping")

    def stop(self):
        """Wait for owned cleanup, preserving manager ownership for later restart."""
        with self.worker_lock:
            self._ensure_open()
            worker = self._begin_stop()
        worker.join()
        if self.last_error:
            raise InvalidInput(self.last_error)
        return {"state": "stopped", "configured": bool(self.state.get("image_id"))}

    def close(self):
        """End this manager lifetime only after background operations have ended."""
        with self.close_lock:
            if self.closed:
                return
            with self.worker_lock:
                self.closing = True
                worker = self._begin_stop()
            worker.join()
            try:
                if self.last_error:
                    raise InvalidInput(self.last_error)
            finally:
                with self.lock:
                    self.closed = True
                    self.lease.release()

    def _cleanup_owned(self):
        with self._operation("stopping", cleanup=True):
            record = self._inspect_owned(cancellable=False)
            if self.state.get("container") and record is None:
                # An absent container is safe to acknowledge only after the daemon
                # positively answers an exact-name query. Connection errors and
                # ownership mismatches retain the recovery receipt.
                names = self._run(["container", "ls", "--all", "--filter", "name=^/" + self.state["container"] + "$", "--format", "{{.Names}}"], timeout=5, cancellable=False).decode().splitlines()
                if names:
                    raise InvalidInput("Could not verify the previous search service. Ownership receipt retained; check Docker before retrying.")
            if record:
                self._run(["stop", "--time", "5", self.state["container"]], timeout=12, cancellable=False)
                self._run(["rm", "--volumes", self.state["container"]], timeout=10, cancellable=False)
            self.state.pop("container", None)
            self.state.pop("nonce", None)
            if self.state:
                _write_private(self.path, self.state)
            self.message = "Owned search service stopped. Its pinned image remains available."
            self.last_error = ""
            return {"state": "stopped", "configured": bool(self.state.get("image_id"))}
