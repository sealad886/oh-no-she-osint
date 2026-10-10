"""An app-owned foreground nginx proxy with private files and loopback listeners."""

import http.client
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import time

from .engine import InvalidInput, private_directory


def nginx_binary():
    candidates = [shutil.which("nginx"), "/opt/homebrew/bin/nginx", "/opt/homebrew/opt/nginx/bin/nginx", "/usr/local/bin/nginx"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).absolute())
    raise InvalidInput("nginx is required by ./start.sh. Install nginx using your package manager (macOS: brew install nginx), then run ./start.sh again. Use ./start.sh --direct only to choose the Python server explicitly.")


class NginxProxy:
    def __init__(self, directory, backend_port, port=0):
        if type(backend_port) is not int or not 1 <= backend_port <= 65535 or type(port) is not int or not 0 <= port <= 65535:
            raise InvalidInput("Proxy ports must be valid local TCP ports.")
        self.directory = private_directory(directory)
        self.binary = nginx_binary()
        self.backend_port = backend_port
        self.port = port
        self.runtime = None
        self.process = None
        self.ready_path = "/__osint_ready_" + secrets.token_hex(16)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    def configuration(self):
        # Relative paths resolve against this process's private -p prefix. No global includes.
        return f"""daemon off;
master_process on;
worker_processes 1;
pid nginx.pid;
error_log error.log crit;
lock_file nginx.lock;
events {{ worker_connections 64; }}
http {{
    access_log off;
    server_tokens off;
    client_max_body_size 3m;
    client_body_timeout 5s;
    client_header_timeout 5s;
    keepalive_timeout 5s;
    client_body_temp_path body;
    proxy_temp_path proxy;
    fastcgi_temp_path fastcgi;
    uwsgi_temp_path uwsgi;
    scgi_temp_path scgi;
    server {{
        listen 127.0.0.1:{self.port};
        server_name 127.0.0.1;
        location = {self.ready_path} {{ return 204; }}
        location / {{
            proxy_pass http://127.0.0.1:{self.backend_port};
            proxy_http_version 1.1;
            proxy_set_header Host $http_host;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_cache off;
            proxy_max_temp_file_size 0;
            proxy_connect_timeout 2s;
            proxy_read_timeout 10s;
            proxy_send_timeout 10s;
        }}
    }}
}}
"""

    def start(self):
        if self.process is not None:
            raise InvalidInput("This local proxy has already been started.")
        # Detect explicit collisions before launching anything; nginx owns the eventual bind.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", self.port))
            self.port = reservation.getsockname()[1]
        self.runtime = Path(tempfile.mkdtemp(prefix="nginx-", dir=self.directory))
        try:
            for name in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
                (self.runtime / name).mkdir(mode=0o700)
            configuration = self.runtime / "nginx.conf"
            fd = os.open(configuration, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(self.configuration())
            # A foreground master and worker in a process group owned by this launch.
            self.process = subprocess.Popen([self.binary, "-p", str(self.runtime) + os.sep, "-c", str(configuration), "-e", str(self.runtime / "error.log")], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise InvalidInput("The app's nginx server could not start. Check port availability and your nginx installation.")
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=0.2)
                try:
                    connection.request("GET", self.ready_path)
                    response = connection.getresponse()
                    response.read()
                    if response.status == 204:
                        return self
                except (OSError, http.client.HTTPException):
                    pass
                finally:
                    connection.close()
                time.sleep(0.02)
            raise InvalidInput("The app's nginx server did not become ready. Check port availability and your nginx installation.")
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.process is not None:
            def terminate_owned(sig):
                try:
                    if hasattr(os, 'killpg'):
                        os.killpg(self.process.pid, sig)
                    elif self.process.poll() is None:
                        self.process.terminate() if sig == signal.SIGTERM else self.process.kill()
                except ProcessLookupError:
                    pass
            terminate_owned(signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                terminate_owned(signal.SIGKILL)
                self.process.wait(timeout=3)
            self.process = None
        if self.runtime is not None:
            # Only this launch's generated files; case evidence remains outside this directory.
            shutil.rmtree(self.runtime)
            self.runtime = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()
