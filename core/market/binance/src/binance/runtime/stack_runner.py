"""Binance Stack 编排器。"""

from __future__ import annotations

from binance.config import BinanceStackConfig
from binance.runtime.hf_runner import build_hf_runner
from binance.runtime.lf_runner import build_lf_runner



def _print_header(action: str, config: BinanceStackConfig) -> None:
    print(f"=== Binance Stack: {action} ===")
    print(f"  shared_entry={config.stack_root / 'src/binance/service_entry.py'}")
    print("  execution_source=unified_runtime")
    print(
        f"  LF(group=lf) enable={'1' if config.enable_lf else '0'} datasets={','.join(config.lf_enabled_dataset_keys) or '-'} runtime_dir={config.lf_runtime_dir}"
    )
    print(
        f"  HF(group=hf) enable={'1' if config.enable_hf else '0'} datasets={','.join(config.hf_enabled_dataset_keys) or '-'} runtime_dir={config.hf_runtime_dir}"
    )



def _print_validation(errors: list[str]) -> None:
    print("❌ Binance Stack 配置校验失败：")
    for item in errors:
        print(f"  - {item}")



def _should_run_group(enabled: bool, dataset_keys: tuple[str, ...], label: str) -> bool:
    if not enabled:
        print(f"  [{label}] disabled")
        return False
    if not dataset_keys:
        print(f"  [{label}] enabled but no datasets")
        return False
    return True



def run_stack_action(action: str, config: BinanceStackConfig) -> int:
    if action in {"start", "restart"}:
        errors = config.validate_start()
        if errors:
            _print_header(action, config)
            _print_validation(errors)
            return 2

    lf_runner = build_lf_runner(config)
    hf_runner = build_hf_runner(config)
    _print_header(action, config)

    rc = 0
    if action == "start":
        if _should_run_group(config.enable_lf, config.lf_enabled_dataset_keys, "LF"):
            rc = max(rc, lf_runner.run("start", dry_run=config.dry_run))
        if _should_run_group(config.enable_hf, config.hf_enabled_dataset_keys, "HF"):
            rc = max(rc, hf_runner.run("start", dry_run=config.dry_run))
        return rc

    if action == "stop":
        rc = max(rc, hf_runner.run("stop", dry_run=config.dry_run))
        rc = max(rc, lf_runner.run("stop", dry_run=config.dry_run))
        return rc

    if action == "status":
        if _should_run_group(config.enable_lf, config.lf_enabled_dataset_keys, "LF"):
            rc = max(rc, lf_runner.run("status", dry_run=config.dry_run))
        if _should_run_group(config.enable_hf, config.hf_enabled_dataset_keys, "HF"):
            rc = max(rc, hf_runner.run("status", dry_run=config.dry_run))
        return rc

    if action == "restart":
        rc = max(rc, hf_runner.run("stop", dry_run=config.dry_run))
        rc = max(rc, lf_runner.run("stop", dry_run=config.dry_run))
        if _should_run_group(config.enable_lf, config.lf_enabled_dataset_keys, "LF"):
            rc = max(rc, lf_runner.run("start", dry_run=config.dry_run))
        if _should_run_group(config.enable_hf, config.hf_enabled_dataset_keys, "HF"):
            rc = max(rc, hf_runner.run("start", dry_run=config.dry_run))
        return rc

    raise RuntimeError(f"不支持的 action: {action}")
