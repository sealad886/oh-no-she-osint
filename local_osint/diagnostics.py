"""Bounded local diagnostics that never retain exception text or request data."""
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import re
import threading
import uuid

from .engine import InvalidInput, now
from .private_files import write_private_json

OPERATIONS = frozenset({"http_request", "search_service_start", "search_service_stop", "investigation"})
EXCEPTIONS = frozenset({"RuntimeError", "KeyError", "AttributeError", "TypeError", "ValueError", "OSError",
                        "PermissionError", "FileNotFoundError", "OperationalError", "IntegrityError",
                        "TimeoutError", "RecursionError", "MemoryError", "Exception"})
MAX_EVENTS = 100


class Diagnostics:
    def __init__(self, directory):
        self.path = Path(directory) / "diagnostics.json"
        self.lock = threading.RLock()
        self.events = deque(maxlen=MAX_EVENTS)
        self.persistence_available = True
        try:
            if self.path.is_symlink():
                raise OSError("Unsafe diagnostics file")
            if self.path.exists():
                if self.path.stat().st_size > 65536:
                    raise ValueError("Oversized diagnostics file")
                data = json.loads(self.path.read_text())
                if not isinstance(data, list):
                    raise ValueError("Invalid diagnostics")
                for entry in data[-MAX_EVENTS:]:
                    if self._valid(entry):
                        self.events.append(entry)
        except (OSError, ValueError, TypeError):
            self.persistence_available = False

    @staticmethod
    def _valid(entry):
        if not isinstance(entry, dict) or set(entry) != {"id", "at", "operation", "exception"}:
            return False
        if not isinstance(entry["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", entry["id"]):
            return False
        if entry["operation"] not in OPERATIONS or entry["exception"] not in EXCEPTIONS:
            return False
        try:
            return isinstance(entry["at"], str) and len(entry["at"]) <= 40 and datetime.fromisoformat(entry["at"]).tzinfo is not None
        except (ValueError, TypeError):
            return False

    def record(self, operation, error):
        kind = type(error).__name__
        entry = {"id": uuid.uuid4().hex, "at": now(),
                 "operation": operation if operation in OPERATIONS else "http_request",
                 "exception": kind if kind in EXCEPTIONS else "Exception"}
        with self.lock:
            self.events.append(entry)
            try:
                write_private_json(self.path, list(self.events))
                self.persistence_available = True
            except (OSError, InvalidInput):
                self.persistence_available = False
        return dict(entry)

    def recent(self):
        with self.lock:
            return [dict(entry) for entry in reversed(self.events)]
