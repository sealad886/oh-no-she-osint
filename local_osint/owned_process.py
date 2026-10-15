"""Exclusive local worker leases and parent-bound process supervision.

The supervisor is deliberately standalone: it can run by absolute filename while
its caller owns stdio/logging. It never invokes a shell or prints child arguments.
"""

import errno
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time


class WorkerLease:
    """An OS-held lock, rather than a PID file that can outlive its owner.

    Keep the object alive for the entire worker lifetime. Never unlink the lock
    file: another process may already have opened that same inode.
    """

    def __init__(self, directory, filename="worker.lock"):
        if not isinstance(filename, str) or not filename or Path(filename).name != filename:
            raise ValueError("Worker lock must use a single file name.")
        self.directory = Path(os.path.abspath(Path(directory).expanduser()))
        self.path = self.directory / filename
        self._fd = None

    @property
    def held(self):
        return self._fd is not None

    def acquire(self):
        if self.held:
            return True
        for component in (self.directory, *self.directory.parents):
            if component.is_symlink():
                raise ValueError("Worker directory must not contain symlinks.")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_info = self.directory.stat()
        self._validate(directory_info, directory=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            self._validate(os.fstat(descriptor))
            if os.name == "nt":
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        return False
                    raise
            else:
                import fcntl
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in {errno.EACCES, errno.EAGAIN}:
                        return False
                    raise
            self._fd = descriptor
            descriptor = None
            return True
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _validate(info, directory=False):
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode) or (not directory and info.st_nlink != 1):
            raise ValueError("Worker state must be a private directory and regular unlinked lock file.")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ValueError("Worker state must belong to the current user.")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Worker directory permissions must be 700 and lock permissions 600.")

    def release(self):
        if not self.held:
            return
        descriptor, self._fd = self._fd, None
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def supervise(parent_pid, arguments, *, interval=0.2, terminate_timeout=5.0):
    """Run one child group until completion, requested stop, or direct parent loss.

    A changed direct PPID is sufficient to detect parent death even if its numeric
    PID gets reused. Signals only target the process/group created here.
    """
    if not isinstance(parent_pid, int) or parent_pid <= 0 or not arguments:
        return 2
    if os.getppid() != parent_pid:
        return 125
    try:
        child = subprocess.Popen(arguments, start_new_session=(os.name == "posix"))
    except OSError:
        return 127
    stopping = False
    deadline = None
    previous = {}

    def send(sig, force=False):
        try:
            if os.name == "posix":
                os.killpg(child.pid, sig)
            elif child.poll() is None:
                child.kill() if force else child.terminate()
        except ProcessLookupError:
            pass

    def request_stop(signum=None, _frame=None):
        nonlocal stopping, deadline
        if not stopping:
            stopping = True
            deadline = time.monotonic() + terminate_timeout
            send(signal.SIGTERM if signum is None else signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, request_stop)
    try:
        while child.poll() is None:
            if os.getppid() != parent_pid:
                request_stop()
            if stopping and time.monotonic() >= deadline:
                send(getattr(signal, "SIGKILL", signal.SIGTERM), force=True)
                child.wait()
                break
            time.sleep(interval)
        # A group leader can exit before its own child. An owned daemon or worker
        # must not leave descendants behind after this supervisor finishes.
        if os.name == "posix":
            send(signal.SIGTERM)
            remaining_deadline = time.monotonic() + terminate_timeout
            while time.monotonic() < remaining_deadline:
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(interval)
            else:
                send(getattr(signal, "SIGKILL", signal.SIGTERM), force=True)
        result = child.wait()
        return 128 - result if result < 0 else result
    finally:
        if child.poll() is None:
            send(getattr(signal, "SIGKILL", signal.SIGTERM), force=True)
            child.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 3 or argv[1] != "--":
        return 2
    try:
        parent_pid = int(argv[0])
    except ValueError:
        return 2
    return supervise(parent_pid, argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
