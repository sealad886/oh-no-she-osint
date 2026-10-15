"""Real process tests for exclusive workers and owned descendant shutdown."""

import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from local_osint.owned_process import WorkerLease
import local_osint.owned_process as owned_process

SUPERVISOR = str(Path(owned_process.__file__).resolve())


class WorkerLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.directory = Path(self.temp.name) / "private"

    def tearDown(self):
        self.temp.cleanup()

    def test_exclusive_lock_requires_owner_release_and_preserves_lock_inode(self):
        first, second = WorkerLease(self.directory), WorkerLease(self.directory)
        try:
            self.assertTrue(first.acquire())
            self.assertTrue(first.held)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            inode = first.path.stat().st_ino
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(first.path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
            first.release()
            self.assertFalse(first.held)
            self.assertTrue(second.acquire())
            self.assertEqual(second.path.stat().st_ino, inode)
        finally:
            first.release()
            second.release()

    def test_process_death_releases_lease_without_stale_pid_recovery(self):
        code = "from local_osint.owned_process import WorkerLease; import sys,time; lease=WorkerLease(sys.argv[1]); print(lease.acquire(),flush=True); time.sleep(60)"
        child = subprocess.Popen([sys.executable, "-c", code, str(self.directory)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lease = WorkerLease(self.directory)
        try:
            self.assertEqual(child.stdout.readline().strip(), "True")
            self.assertFalse(lease.acquire())
            child.kill()
            child.wait(timeout=5)
            self.assertTrue(lease.acquire())
        finally:
            lease.release()
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.communicate()

    @unittest.skipUnless(os.name == "posix", "POSIX filesystem mode/symlink contract")
    def test_refuses_shared_permissions_symlinks_and_hardlinks(self):
        self.directory.mkdir(mode=0o700)
        self.directory.chmod(0o755)
        with self.assertRaises(ValueError):
            WorkerLease(self.directory).acquire()
        self.directory.chmod(0o700)
        target = self.directory / "target"
        target.write_text("private")
        target.chmod(0o600)
        link = self.directory / "worker.lock"
        link.symlink_to(target)
        with self.assertRaises((ValueError, OSError)):
            WorkerLease(self.directory).acquire()
        link.unlink()
        os.link(target, link)
        with self.assertRaises(ValueError):
            WorkerLease(self.directory).acquire()
        link.unlink()
        link.write_text("")
        link.chmod(0o644)
        with self.assertRaises(ValueError):
            WorkerLease(self.directory).acquire()


@unittest.skipUnless(os.name == "posix", "Native POSIX process and signal lifecycle")
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.root = Path(self.temp.name)
        self.processes = []
        self.child_program = self.root / "child.py"
        self.child_program.write_text("""import os,signal,sys,time
from pathlib import Path
root=Path(sys.argv[1])
root.joinpath('child.pid').write_text(str(os.getpid()))
def stop(signum,frame):
    root.joinpath('terminated').write_text(str(signum))
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
root.joinpath('ready').write_text('ready')
while True: time.sleep(.05)
""")

    def tearDown(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.temp.cleanup()

    def await_file(self, name, timeout=8):
        deadline = time.monotonic() + timeout
        path = self.root / name
        while time.monotonic() < deadline:
            if path.exists():
                return path.read_text()
            time.sleep(.03)
        self.fail("Owned child did not reach expected lifecycle event: " + name)

    def launch(self):
        process = subprocess.Popen([sys.executable, SUPERVISOR, str(os.getpid()), "--", sys.executable, str(self.child_program), str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        self.await_file("ready")
        return process

    def test_normal_stop_forwards_signal_and_does_not_touch_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(unrelated)
        supervisor = self.launch()
        supervisor.terminate()
        self.assertEqual(supervisor.wait(timeout=10), 0)
        self.assertEqual(int(self.await_file("terminated")), signal.SIGTERM)
        self.assertIsNone(unrelated.poll())

    def test_parent_forced_death_stops_owned_child(self):
        launcher = self.root / "launcher.py"
        launcher.write_text("""import os,subprocess,sys,time
from pathlib import Path
process=subprocess.Popen([sys.executable,sys.argv[1],str(os.getpid()),'--',sys.executable,sys.argv[2],sys.argv[3]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path(sys.argv[3]).joinpath('supervisor.pid').write_text(str(process.pid))
while True:time.sleep(.1)
""")
        parent = subprocess.Popen([sys.executable, str(launcher), SUPERVISOR, str(self.child_program), str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(parent)
        self.await_file("ready")
        parent.kill()
        parent.wait(timeout=5)
        self.assertEqual(int(self.await_file("terminated")), signal.SIGTERM)

    def test_direct_parent_mismatch_does_not_spawn_child(self):
        result = subprocess.run([sys.executable, SUPERVISOR, str(os.getpid() + 1000000), "--", sys.executable, str(self.child_program), str(self.root)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 125)
        self.assertFalse((self.root / "child.pid").exists())
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_unresponsive_child_is_killed_after_grace_period(self):
        self.child_program.write_text("""import signal,sys,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path(sys.argv[1]).joinpath('ready').write_text('ready')
while True: time.sleep(.05)
""")
        supervisor = self.launch()
        started = time.monotonic()
        supervisor.terminate()
        self.assertEqual(supervisor.wait(timeout=9), 128 + signal.SIGKILL)
        self.assertGreaterEqual(time.monotonic() - started, 4.5)

    def test_child_exit_status_and_exec_failure_are_preserved_without_argument_logs(self):
        result = subprocess.run([sys.executable, SUPERVISOR, str(os.getpid()), "--", sys.executable, "-c", "raise SystemExit(17)"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 17)
        failure = subprocess.run([sys.executable, SUPERVISOR, str(os.getpid()), "--", str(self.root / "missing-secret-command")], capture_output=True, text=True, timeout=5)
        self.assertEqual(failure.returncode, 127)
        self.assertEqual(failure.stderr, "")


if __name__ == "__main__":
    unittest.main()
