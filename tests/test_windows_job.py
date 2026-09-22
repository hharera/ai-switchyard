import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

import pytest

from ninerouter_orchestrator import windows_job


class FakeJob:
    def __init__(self):
        self.assigned = False
        self.closed = False
        self.process = None

    def assign(self, process):
        self.process = process
        self.assigned = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_job(monkeypatch):
    job = FakeJob()
    monkeypatch.setattr(windows_job, "WindowsJob", lambda: job)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    return job


def test_gated_launcher_preserves_output_and_exit_status(fake_job, tmp_path):
    process, job = windows_job.launch_in_job(
        [sys.executable, "-c", "import sys; print('ready'); sys.exit(7)"],
        cwd=tmp_path, env=os.environ.copy(),
    )
    try:
        assert job.assigned
        assert process.wait(timeout=5) == 7
        assert process.stdout.read().strip() == b"ready"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        job.close()


def test_failed_job_assignment_never_launches_user_command(fake_job, monkeypatch, tmp_path):
    marker = tmp_path / "must-not-exist"

    def fail_assignment(process):
        fake_job.process = process
        raise OSError("Job assignment rejected")

    monkeypatch.setattr(fake_job, "assign", fail_assignment)
    with pytest.raises(OSError, match="assignment rejected"):
        windows_job.launch_in_job(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            cwd=tmp_path, env=os.environ.copy(),
        )
    assert not marker.exists()
    assert fake_job.closed
    assert fake_job.process.poll() is not None
    assert fake_job.process.stdin.closed
    assert fake_job.process.stdout.closed


def test_launch_failure_closes_job(fake_job, monkeypatch, tmp_path):
    def fail_launch(*args, **kwargs):
        raise OSError("No Python executable")

    monkeypatch.setattr(subprocess, "Popen", fail_launch)
    with pytest.raises(OSError, match="No Python executable"):
        windows_job.launch_in_job(["cmd.exe"], cwd=tmp_path, env=os.environ.copy())
    assert fake_job.closed


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows Job Objects")
@pytest.mark.parametrize("cleanup", ["terminate", "close"])
def test_native_job_cleans_up_children_after_parent_exits(tmp_path, cleanup):
    script = tmp_path / "parent with spaces.py"
    script.write_text(
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print(child.pid, flush=True)\n",
        encoding="utf-8",
    )
    process, job = windows_job.launch_in_job(
        [sys.executable, str(script)], cwd=tmp_path, env=os.environ.copy()
    )
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    child_handle = None
    try:
        child_pid = int(process.stdout.readline())
        child_handle = api.OpenProcess(0x100000, False, child_pid)  # SYNCHRONIZE
        assert child_handle
        assert process.wait(timeout=5) == 0
        assert api.WaitForSingleObject(child_handle, 0) == 258  # WAIT_TIMEOUT: still alive
        getattr(job, cleanup)()
        assert api.WaitForSingleObject(child_handle, 5000) == 0
    finally:
        job.close()
        process.wait(timeout=5)
        process.stdout.close()
        if child_handle:
            api.CloseHandle(child_handle)
