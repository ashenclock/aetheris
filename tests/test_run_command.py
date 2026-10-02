import asyncio
import os
import shlex
import subprocess
import sys
import time

import pytest

from nexus.skills.run_command import RunCommandSkill


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX bash pipeline semantics")
async def test_pipeline_preserves_upstream_failure(tmp_path):
    result = await RunCommandSkill(tmp_path).execute(command="false | cat")
    assert result.startswith("Error: command exited with code 1")


@pytest.mark.asyncio
async def test_large_stdout_and_stderr_are_drained_but_bounded(tmp_path):
    code = (
        "import sys; sys.stdout.write('x' * 5000000); sys.stderr.write('y' * 5000000)"
    )
    command = " ".join(shlex.quote(part) for part in (sys.executable, "-c", code))

    result = await RunCommandSkill(tmp_path).execute(command=command, timeout=5)

    assert "Exit code: 0" in result
    assert "[stdout truncated]" in result
    assert "[stderr truncated]" in result
    assert len(result) < 4_000


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cleanup")
async def test_timeout_kills_shell_descendants_and_closes_pipes(tmp_path):
    pid_file = tmp_path / "child.pid"
    child_code = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"open({str(pid_file)!r},'w').write(str(child.pid)); time.sleep(60)"
    )
    command = " ".join(
        shlex.quote(part) for part in (sys.executable, "-c", parent_code)
    )
    skill = RunCommandSkill(tmp_path)

    started = time.monotonic()
    result = await skill.execute(command=command, timeout=1)
    elapsed = time.monotonic() - started

    assert "timed out after 1 seconds" in result
    assert elapsed < 4
    child_pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child_pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail(f"Shell descendant {child_pid} survived timeout cleanup")


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cleanup")
async def test_cancellation_kills_shell_descendants(tmp_path):
    pid_file = tmp_path / "cancelled-child.pid"
    child_code = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"open({str(pid_file)!r},'w').write(str(child.pid)); time.sleep(60)"
    )
    command = " ".join(
        shlex.quote(part) for part in (sys.executable, "-c", parent_code)
    )
    task = asyncio.create_task(RunCommandSkill(tmp_path).execute(command=command))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    else:
        task.cancel()
        pytest.fail("Shell command did not start its child process")

    child_pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(50):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child_pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail(f"Cancelled shell descendant {child_pid} survived cleanup")


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cleanup")
async def test_timeout_kills_child_after_shell_leader_exits(tmp_path):
    pid_file = tmp_path / "orphaned-pipe-child.pid"
    child_code = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"open({str(pid_file)!r},'w').write(str(child.pid))"
    )
    command = " ".join(
        shlex.quote(part) for part in (sys.executable, "-c", parent_code)
    )

    result = await RunCommandSkill(tmp_path).execute(command=command, timeout=1)

    assert "timed out after 1 seconds" in result
    child_pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child_pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail(f"Child holding the output pipe {child_pid} survived timeout")
