from __future__ import annotations

from pathlib import Path
import subprocess


def test_legacy_shell_guard_enforces_single_root() -> None:
    stack_dir = Path(__file__).resolve().parents[1]
    guard = stack_dir / "scripts" / "check_structure_contract.sh"
    result = subprocess.run([str(guard)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
