"""运行时进程工具。"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

STOP_TIMEOUT = 10


def ensure_parent_dirs(*paths: Path) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)


def read_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def write_pid(pid_file: Path, pid: int) -> None:
    ensure_parent_dirs(pid_file)
    pid_file.write_text(f"{pid}\n", encoding="utf-8")


def unlink_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def is_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def choose_python_bin(runtime_dir: Path, *, stack_root: Path | None = None) -> str:
    candidates = [runtime_dir / '.venv' / 'bin' / 'python3']
    if stack_root is not None:
        candidates.append(stack_root / '.venv' / 'bin' / 'python3')
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable or 'python3'


def build_runtime_env(stack_src: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    current = env.get('PYTHONPATH', '').strip()
    stack_src_str = str(stack_src)
    project_root_str = str(stack_src.parents[3])
    parts: list[str] = [stack_src_str, project_root_str]
    if current:
        parts.append(current)
    env['PYTHONPATH'] = ":".join(parts)
    env.setdefault('BINANCE_STACK_EXECUTION_SOURCE', 'unified_runtime')
    if extra:
        env.update({key: str(value) for key, value in extra.items()})
    return env


def start_detached(*, command: list[str], cwd: Path, env: dict[str, str], log_file: Path) -> int:
    ensure_parent_dirs(log_file)
    with log_file.open('a', encoding='utf-8') as handle:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    return int(process.pid)


def stop_process(pid: int | None, *, timeout: int = STOP_TIMEOUT) -> bool:
    if not is_running(pid):
        return True

    assert pid is not None
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True

    waited = 0
    while waited < timeout:
        if not is_running(pid):
            return True
        time.sleep(1)
        waited += 1

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return True
    return not is_running(pid)


def format_uptime(pid: int | None) -> str:
    if not is_running(pid):
        return 'unknown'
    assert pid is not None
    result = subprocess.run(
        ['ps', '-o', 'etime=', '-p', str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or '').strip()
    return output or 'unknown'
