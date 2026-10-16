"""Atomic, owner-only JSON state shared by local runtime services."""
import json
import os
from pathlib import Path
import secrets

from .engine import InvalidInput


def write_private_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise InvalidInput("Local state files must not be symlinks.")
    temporary = path.with_name(path.name + "." + secrets.token_hex(6))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
