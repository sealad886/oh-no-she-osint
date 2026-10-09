"""Bounded authenticated single-user loopback service, never a remote deployment."""

import base64
import binascii
import hmac
import json
import secrets
import socket
import sqlite3
import threading
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .collector import collect, research, discover
from .engine import InvalidInput, MAX_BYTES

STATIC = Path(__file__).parent / "static"
MAX_REQUEST = 3 * 1024 * 1024


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, engine, port=0, allow_network=False):
        self.engine = engine
        self.allow_network = allow_network
        self.token = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.host = f"127.0.0.1:{self.server_port}"

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
        # No private request data or exception tracebacks in routine logs.
        pass


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
                elif path == "/api/cases":
                    self.reply(200, engine.cases())
                elif path == "/api/view":
                    self.reply(200, engine.view(case_id, params.get("q", [""])[0], params.get("kind", [""])[0]))
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
                if path == "/api/cases":
                    self.reply(201, engine.create_case(data.get("name"), data.get("focus_type", "topic"), data.get("aliases"), data.get("identifiers")))
                elif path == "/api/import":
                    value = data.get("original_base64")
                    if not isinstance(value, str):
                        raise InvalidInput("Choose a file or paste text.")
                    try:
                        raw = base64.b64decode(value, validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise InvalidInput("Invalid evidence encoding.") from exc
                    self.reply(201, engine.ingest(data.get("case"), raw, data.get("title"), data.get("source", "Imported file"), data.get("format", "text")))
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
