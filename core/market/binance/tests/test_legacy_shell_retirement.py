from __future__ import annotations

from pathlib import Path

from binance.config import load_stack_config


def test_single_engineering_root_contract() -> None:
    stack_dir = Path(__file__).resolve().parents[1]

    assert not (stack_dir / "lf").exists()
    assert not (stack_dir / "hf").exists()
    assert not (stack_dir / "data").exists()

    config = load_stack_config()
    assert config.lf_runtime_dir == stack_dir / "var" / "lf"
    assert config.hf_runtime_dir == stack_dir / "var" / "hf"
