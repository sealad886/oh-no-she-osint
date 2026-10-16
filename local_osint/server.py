"""Bounded authenticated single-user loopback service, never a remote deployment."""

import base64
import binascii
import hmac
import json
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .collector import collect, research, discover
from .engine import InvalidInput, MAX_BYTES
from .investigation import Jobs
from .ai import LocalAI
from .workspace import Workspace
from .search_service import SearchService
from .diagnostics import Diagnostics

STATIC = Path(__file__).parent / "static"
MAX_REQUEST = 3 * 1024 * 1024


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, engine, port=0, allow_network=False, model_path=None, shutdown_on_close=False,
                 page_timeout=90, launch_grace=120, close_grace=2):
        self.engine = engine
        self.allow_network = allow_network
        self.token = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(8)
        self.shutdown_on_close = shutdown_on_close
        self.page_timeout, self.launch_grace = page_timeout, launch_grace
        self.page_close_grace = close_grace
        self._page_lock = threading.RLock()
        self._page_leases = {}
        self._page_seen = False
        self._page_started = time.monotonic()
        self._page_shutdown_started = False
        self._page_close_deadline = None
        self._lifecycle_stop = threading.Event()
        self._watchdog = None
        self._close_lock = threading.Lock()
        self._runtime_closed = False
        self.diagnostics = Diagnostics(engine.directory)
        try:
            super().__init__(("127.0.0.1", port), Handler)
            self.origin = f"http://127.0.0.1:{self.server_port}"
            self.host = f"127.0.0.1:{self.server_port}"
            self.ai = LocalAI(engine, model_path)
            self.workspace = Workspace(engine)
            self.search_service = SearchService(engine.directory, diagnostics=self.diagnostics)
            self.jobs = Jobs(engine, allow_network, self.ai, self.search_service, diagnostics=self.diagnostics)
            if shutdown_on_close:
                self._watchdog = threading.Thread(target=self._watch_pages, name='osint-page-watchdog', daemon=True)
                self._watchdog.start()
        except BaseException:
            self.server_close()
            raise

    def server_close(self):
        with self._close_lock:
            if self._runtime_closed:
                return
            self._runtime_closed = True
            self._lifecycle_stop.set()
        if self._watchdog and self._watchdog is not threading.current_thread():
            self._watchdog.join(timeout=2)
        try:
            if hasattr(self, 'jobs'):
                self.jobs.close()
        finally:
            try:
                if hasattr(self, 'ai'):
                    self.ai.stop()
            finally:
                try:
                    if hasattr(self, 'search_service'):
                        self.search_service.close()
                finally:
                    super().server_close()

    def _request_page_shutdown(self):
        # shutdown() waits for serve_forever(); never call it from an HTTP handler's serving thread.
        with self._page_lock:
            if not self.shutdown_on_close or self._page_shutdown_started or self._lifecycle_stop.is_set():
                return
            self._page_shutdown_started = True
        threading.Thread(target=self.shutdown, name='osint-page-shutdown', daemon=True).start()

    def page_lifecycle(self, action, data):
        if set(data) != {'id'} or not isinstance(data['id'], str) or not re.fullmatch(
                r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', data['id']):
            raise InvalidInput('Page lifecycle requests require one browser UUID.')
        page_id = data['id'].lower()
        with self._page_lock:
            if self._page_shutdown_started:
                raise InvalidInput('This app session is closing. Relaunch the app for a new session.')
            known = page_id in self._page_leases
            if action == 'open':
                if not known and len(self._page_leases) >= 32:
                    raise InvalidInput('This app session supports up to 32 open pages.')
                self._page_leases[page_id] = time.monotonic()
                self._page_seen = True
                self._page_close_deadline = None
                known = True
            elif action == 'heartbeat' and known:
                self._page_leases[page_id] = time.monotonic()
            elif action == 'close' and known:
                del self._page_leases[page_id]
                if not self._page_leases:
                    # Reload and same-app navigation also emit pagehide. Give the new document time to register.
                    self._page_close_deadline = time.monotonic() + self.page_close_grace
            return {'ok': True, 'registered': known if action != 'close' else False,
                    'pages': len(self._page_leases), 'closing': self._page_shutdown_started}

    def _watch_pages(self):
        interval = max(0.02, min(0.25, self.page_timeout / 3, self.launch_grace / 3, self.page_close_grace / 3))
        while not self._lifecycle_stop.wait(interval):
            with self._page_lock:
                current = time.monotonic()
                expired = [page_id for page_id, heartbeat in self._page_leases.items()
                           if current - heartbeat >= self.page_timeout]
                for page_id in expired:
                    del self._page_leases[page_id]
                if (self._page_seen and not self._page_leases and (
                        self._page_close_deadline is None or current >= self._page_close_deadline)) or (
                        not self._page_seen and current - self._page_started >= self.launch_grace):
                    self._request_page_shutdown()
                    return

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        error = sys.exception()
        # A browser disconnect is expected during departure; unexpected failures
        # retain only safe metadata, never request content or exception text.
        if error and not isinstance(error, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            self.diagnostics.record('http_request', error)


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalOSINT"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    def reply(self, status, body, mime="application/json; charset=utf-8", cookie=None, attachment=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=True).encode() if mime.startswith("application/json") else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        if self.headers.get_all("Host", []) != [self.server.host]:
            self.reply(403, {"error": "Invalid local host."})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            self.reply(403, {"error": "Cross-origin requests are blocked."})
            return False
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in {"none", "same-origin"}:
            self.reply(403, {"error": "Open the app directly on this machine."})
            return False
        return True

    def authenticated(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie["osint_session"].value if "osint_session" in cookie else ""
        except CookieError:
            token = ""
        if not hmac.compare_digest(token.encode(), self.server.token.encode()) or self.headers.get("X-OSINT-Local") != "1":
            self.reply(401, {"error": "Unlock this workspace using the launch link."})
            return False
        return True

    def body(self):
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            raise InvalidInput("A single Content-Length is required.")
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise InvalidInput("Requests must use JSON.")
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= MAX_REQUEST:
            raise InvalidInput("Request must contain 1 byte to 3 MiB.")
        raw = self.rfile.read(int(length))
        if len(raw) != int(length):
            raise InvalidInput("Incomplete request.")
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise InvalidInput("Invalid JSON request.") from exc
        if not isinstance(value, dict):
            raise InvalidInput("Request must be a JSON object.")
        return value

    def do_GET(self):
        self.handle_route("GET")

    def do_POST(self):
        self.handle_route("POST")

    def handle_route(self, method):
        try:
            if not self.trusted():
                return
            parsed = urlsplit(self.path)
            path = parsed.path
            if method == "GET" and path in {"/", "/app.js", "/style.css"}:
                filename, mime = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}[path]
                self.reply(200, (STATIC / filename).read_bytes(), mime)
                return
            if method == "POST" and path == "/api/session":
                if self.headers.get("Origin") != self.server.origin or self.headers.get("X-OSINT-Local") != "1":
                    self.reply(403, {"error": "Session exchange requires the local app."})
                    return
                value = self.body().get("token", "")
                if not isinstance(value, str) or not hmac.compare_digest(value.encode(), self.server.token.encode()):
                    self.reply(401, {"error": "Launch link expired or incorrect. Restart the app for a new link."})
                    return
                self.reply(200, {"ok": True}, cookie=f"osint_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/")
                return
            if not path.startswith("/api/"):
                self.reply(404, {"error": "Page not found."})
                return
            if not self.authenticated():
                return
            if method == "POST" and self.headers.get("Origin") != self.server.origin:
                self.reply(403, {"error": "Local origin required."})
                return
            params = parse_qs(parsed.query, keep_blank_values=True)
            engine = self.server.engine
            case_id = params.get("case", [""])[0]
            if method == "GET":
                if path == "/api/status":
                    self.reply(200, {"network": self.server.allow_network, "max_bytes": MAX_BYTES, "version": "0.1.0"})
                elif path == "/api/prompts":
                    from .prompts import DEFAULT_PROMPTS
                    from .collector import SEARCH_PROVIDERS
                    self.reply(200, {'defaults': DEFAULT_PROMPTS, 'providers': [
                        {'id': key, 'label': label} for key, label in SEARCH_PROVIDERS.items()]})
                elif path == "/api/cases":
                    self.reply(200, engine.cases())
                elif path == '/api/connectors':
                    from .connectors import catalogue
                    service = self.server.search_service.status()
                    self.reply(200, {'connectors': catalogue({'search_service': self.server.search_service}, service_status=service),
                        'search_service': service})
                elif path == '/api/search-service':
                    self.reply(200, self.server.search_service.status())
                elif path == '/api/diagnostics':
                    self.reply(200, {'events': self.server.diagnostics.recent(),
                                     'persistent': self.server.diagnostics.persistence_available})
                elif path == '/api/workspace':
                    from .frontier import default_settings
                    with engine.connection() as db:
                        case = engine.require_case(db, case_id)
                    state = self.server.workspace.snapshot(case_id)
                    state['settings'] = {**default_settings(case), **state['settings']}
                    for task in state['tasks']:
                        task['attempt_history'] = self.server.workspace.attempts(case_id, task['id'])
                    self.reply(200, state)
                elif path == "/api/view":
                    self.reply(200, engine.view(case_id, params.get("q", [""])[0], params.get("kind", [""])[0]))
                elif path == "/api/investigation":
                    self.reply(200, self.server.jobs.get(case_id))
                elif path == "/api/ai":
                    self.reply(200, self.server.ai.status())
                elif path == "/api/evidence":
                    self.reply(200, engine.evidence(case_id, params.get("id", [""])[0]))
                elif path == "/api/entity":
                    self.reply(200, engine.entity(case_id, params.get("id", [""])[0]))
                elif path == "/api/export":
                    if params.get("format", ["json"])[0] == "markdown":
                        self.reply(200, engine.markdown(case_id), "text/markdown; charset=utf-8", attachment="case-report.md")
                    else:
                        self.reply(200, engine.export(case_id), attachment="case-evidence.json")
                else:
                    self.reply(404, {"error": "Endpoint not found."})
            elif method == "POST":
                data = self.body()
                if path in {'/api/page/open', '/api/page/heartbeat', '/api/page/close'}:
                    self.reply(200, self.server.page_lifecycle(path.rsplit('/', 1)[-1], data))
                elif path == "/api/cases":
                    self.reply(201, engine.create_case(data.get("name"), data.get("focus_type", "topic"), data.get("aliases"), data.get("identifiers")))
                elif path == '/api/workspace/settings':
                    from .frontier import SOURCE_KEYS, source_settings
                    settings = data.get('settings')
                    if not isinstance(settings, dict) or set(settings) - SOURCE_KEYS:
                        raise InvalidInput('Choose supported source settings.')
                    self.reply(200, self.server.workspace.configure(data.get('case'), source_settings(settings)))
                elif path == '/api/action':
                    from .connectors import validate_arguments
                    args = validate_arguments(data.get('connector'), data.get('arguments'))
                    self.reply(202, self.server.jobs.start(data.get('case'), action={'tool': data['connector'], 'arguments': args}))
                elif path == '/api/resume':
                    self.reply(202, self.server.jobs.start(data.get('case'), action={'tool': 'resume_queue', 'arguments': {}}))
                elif path == '/api/candidate/decision':
                    case = data.get('case')
                    decision = self.server.workspace.decide_candidate(case, data.get('candidate'), data.get('status'), reason=data.get('reason', ''))
                    snapshot = self.server.workspace.snapshot(case)
                    same = any(item['status'] == 'same' for item in snapshot['candidates'])
                    excluded = {item['source_id'] for item in snapshot['candidates'] if item['status'] == 'different' and item.get('source_id')}
                    for question in snapshot['questions']:
                        if question['key'] == 'identity':
                            self.server.workspace.update_question(case, question['id'], status='supported' if same else 'unresolved',
                                gap='An operator match decision is recorded; individual claims still require evidence.' if same else 'Possible matches remain uncertain; compare the original passages.')
                        elif question['key'] == 'objective' and excluded.intersection(question['evidence_ids']):
                            self.server.workspace.update_question(case, question['id'], status='unresolved', gap='A cited candidate was marked different. Continue investigation to reassess this answer; earlier passages remain saved.')
                    self.reply(200, decision)
                elif path == '/api/document/reextract':
                    if self.server.jobs.active:
                        raise InvalidInput('Stop collection before retrying document extraction.')
                    self.reply(200, engine.reextract(data.get('case'), data.get('id'), data.get('document_options')))
                elif path == '/api/task':
                    if self.server.jobs.active:
                        raise InvalidInput('Pause the investigation before editing queued work.')
                    operation = data.get('operation')
                    if operation not in {'retry', 'skip', 'priority', 'edit'}:
                        raise InvalidInput('Choose retry, skip, edit or priority.')
                    task = self.server.workspace.edit_action(data.get('case'), data.get('id'),
                        arguments=data.get('arguments') if operation == 'edit' else None,
                        priority=data.get('priority') if operation == 'priority' else None,
                        state='pending' if operation in {'retry', 'edit'} else 'skipped' if operation == 'skip' else None)
                    if operation in {'retry', 'edit'}:
                        settings = self.server.workspace.settings(data.get('case'))
                        self.server.workspace.configure(data.get('case'), {'approved_actions': list(dict.fromkeys([*settings.get('approved_actions', []), task['id']]))})
                        self.reply(202, self.server.jobs.start(data.get('case'), action={'tool': 'resume_queue', 'arguments': {'id': task['id']}}))
                    else:
                        self.reply(200, task)
                elif path == '/api/search-service/start':
                    if not self.server.allow_network:
                        raise InvalidInput('Offline mode does not permit starting an external search service.')
                    if self.server.jobs.active:
                        raise InvalidInput('Stop collection before changing the search service.')
                    self.reply(202, self.server.search_service.start_async(data.get('settings')))
                elif path == '/api/search-service/stop':
                    self.reply(202, self.server.search_service.stop_async())
                elif path == "/api/investigate":
                    options = data.get('options')
                    if options is not None and not isinstance(options, dict):
                        raise InvalidInput('Investigation settings must be an object.')
                    self.reply(202, self.server.jobs.start(data.get('case'), data.get('ai', True), {**(options or {}), 'automatic': True}))
                elif path == "/api/continue":
                    options = data.get('options')
                    if options is not None and not isinstance(options, dict):
                        raise InvalidInput('Investigation settings must be an object.')
                    self.reply(202, self.server.jobs.start(data.get('case'), data.get('ai', True), options={**(options or {}), 'automatic': True}, continuing=True))
                elif path == '/api/tool':
                    self.reply(202, self.server.jobs.start(data.get('case'), data.get('ai', False),
                        action={'tool': data.get('tool'), 'arguments': data.get('arguments', {})}))
                elif path == "/api/stop":
                    self.reply(200, self.server.jobs.stop(data.get('case'), data.get('run_id')))
                elif path == "/api/ai/start":
                    self.reply(202, self.server.ai.start())
                elif path == "/api/ai/stop":
                    self.reply(200, self.server.ai.stop())
                elif path == "/api/import":
                    value = data.get("original_base64")
                    if not isinstance(value, str):
                        raise InvalidInput("Choose a file or paste text.")
                    try:
                        raw = base64.b64decode(value, validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise InvalidInput("Invalid evidence encoding.") from exc
                    self.reply(201, engine.ingest(data.get("case"), raw, data.get("title"), data.get("source", "Imported file"), data.get("format", "text"), document_options=data.get('document_options')))
                elif path == "/api/discover":
                    self.reply(200, discover(engine, data.get("case"), data.get("sources"), self.server.allow_network, data.get("query")))
                elif path == "/api/research":
                    self.reply(200, research(engine, data.get("case"), data.get("domain"), data.get("signals"), self.server.allow_network))
                elif path == "/api/collect":
                    self.reply(201, collect(engine, data.get("case"), data.get("url"), self.server.allow_network))
                else:
                    self.reply(404, {"error": "Endpoint not found."})
        except InvalidInput as exc:
            self.reply(400, {"error": str(exc)})
        except (socket.timeout, TimeoutError):
            self.reply(408, {"error": "Request timed out. Try again."})
        except (sqlite3.Error, OSError):
            self.reply(503, {"error": "Local storage unavailable. Check disk space and permissions."})
        except (TypeError, ValueError, OverflowError):
            self.reply(400, {"error": "Invalid request fields."})
        except Exception as exc:
            event = self.server.diagnostics.record('http_request', exc)
            self.reply(500, {"error": "Local operation failed unexpectedly. Saved evidence is retained. See Local diagnostics. Reference " + event['id'],
                             "diagnostic_id": event['id']})
