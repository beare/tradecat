"""统一内部服务入口。"""

from __future__ import annotations

import argparse
import sys

from binance.config import load_stack_config
from binance.registry import is_dataset_enabled, list_datasets
from binance.runtime.hf_canary import render_hf_canary_plan, render_hf_canary_summary, rollback_hf_canary, verify_hf_canary
from binance.runtime.audit import audit_exit_code, doctor_exit_code, render_audit_summary, render_doctor_health
from binance.runtime.stack_runner import run_stack_action


def _render_dataset_table() -> str:
    headers = [
        "dataset_key",
        "group",
        "runtime_status",
        "enabled",
        "enabled_env",
        "collect",
        "backfill",
        "repair",
    ]
    rows = [
        [
            spec.dataset_key,
            spec.group,
            spec.runtime_status,
            "1" if is_dataset_enabled(spec) else "0",
            spec.enabled_env,
            spec.collect_cli or "-",
            spec.backfill_cli or "-",
            spec.repair_cli or "-",
        ]
        for spec in list_datasets(include_reserved=True)
    ]

    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def border(sep: str = "-") -> str:
        return "+" + "+".join(sep * (width + 2) for width in widths) + "+"

    def render_row(values: list[str]) -> str:
        return "| " + " | ".join(value.ljust(widths[index]) for index, value in enumerate(values)) + " |"

    parts = [border(), render_row(headers), border("=")]
    parts.extend(render_row(row) for row in rows)
    parts.append(border())
    return "\n".join(parts)


def _print_runtime_summary(config) -> None:
    print(f"  env_file={config.env_file or '-'}")
    print(f"  lf_enabled={'1' if config.enable_lf else '0'}")
    print(f"  hf_enabled={'1' if config.enable_hf else '0'}")
    print(f"  hf_service_mode={config.hf_service_mode or '-'}")
    print(f"  lf_runtime_dir={config.lf_runtime_dir}")
    print(f"  hf_runtime_dir={config.hf_runtime_dir}")
    print(f"  lf_enabled_datasets={','.join(config.lf_enabled_dataset_keys) or '-'}")
    print(f"  hf_enabled_datasets={','.join(config.hf_enabled_dataset_keys) or '-'}")


def _run_doctor() -> int:
    config = load_stack_config()
    print("=== Binance Stack: doctor ===")
    _print_runtime_summary(config)
    print("=== HF canary ===")
    print(render_hf_canary_summary(config))
    print(_render_dataset_table())
    if config.enable_lf and config.lf_enabled_dataset_keys:
        rows, rendered = render_doctor_health(config)
        print("=== LF dataset health ===")
        print(rendered)
    errors = config.validate_start()
    if errors:
        print("  validation:")
        for item in errors:
            print(f"  - {item}")
        return 2
    print("  validation: ok")
    if config.enable_lf and config.lf_enabled_dataset_keys:
        return doctor_exit_code(rows)
    return 0


def _run_audit() -> int:
    config = load_stack_config()
    print("=== Binance Stack: audit ===")
    _print_runtime_summary(config)
    if not config.enable_lf or not config.lf_enabled_dataset_keys:
        print("  lf_audit=skipped")
        return 0
    rows, rendered = render_audit_summary(config)
    print(rendered)
    return audit_exit_code(rows)


def _run_canary(action: str) -> int:
    config = load_stack_config()
    if action == "plan":
        print(render_hf_canary_plan(config))
        return 0 if not config.validate_hf_canary() else 2
    if action == "verify":
        rc, rendered = verify_hf_canary(config)
        print(rendered)
        return rc
    if action == "rollback":
        rc, rendered = rollback_hf_canary(config)
        print(rendered)
        return rc
    raise RuntimeError(f"不支持的 canary action: {action}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="binance.service_entry")
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["start", "stop", "status", "restart", "plan", "doctor", "audit", "canary"],
    )
    parser.add_argument("subaction", nargs="?", default="plan")
    args = parser.parse_args()

    if args.action == "plan":
        print("=== Binance Stack: plan ===")
        config = load_stack_config()
        _print_runtime_summary(config)
        print(_render_dataset_table())
        errors = config.validate_start()
        if errors:
            print("  validation:")
            for item in errors:
                print(f"  - {item}")
        return 0

    if args.action == "doctor":
        return _run_doctor()
    if args.action == "audit":
        return _run_audit()
    if args.action == "canary":
        return _run_canary(args.subaction)

    config = load_stack_config()
    return run_stack_action(args.action, config)


if __name__ == "__main__":
    sys.exit(main())
