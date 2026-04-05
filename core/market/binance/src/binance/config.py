"""统一控制面配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from binance.common.env import build_layout, bool_env, load_env_defaults
from binance.common.hf_env import hf_env, primary_hf_env_name
from binance.registry import enabled_dataset_keys, find_dataset_by_cli


STACK_ROOT = Path(__file__).resolve().parents[2]
LAYOUT = build_layout(STACK_ROOT)
load_env_defaults(LAYOUT.env_file)


@dataclass(frozen=True, slots=True)
class BinanceStackConfig:
    project_root: Path
    stack_root: Path
    env_file: Path | None
    var_root: Path
    lf_runtime_dir: Path
    hf_runtime_dir: Path
    enable_lf: bool
    enable_hf: bool
    dry_run: bool
    hf_service_mode: str

    @property
    def lf_enabled_dataset_keys(self) -> tuple[str, ...]:
        return enabled_dataset_keys(group="lf")

    @property
    def hf_enabled_dataset_keys(self) -> tuple[str, ...]:
        return enabled_dataset_keys(group="hf")

    def validate_start(self) -> list[str]:
        errors: list[str] = []

        if self.enable_lf and not self.lf_enabled_dataset_keys:
            errors.append("LF 已开启，但没有任何启用的数据集")

        if not self.enable_hf:
            return errors

        if not self.hf_enabled_dataset_keys:
            errors.append("HF 已开启，但没有任何启用的数据集")
            return errors

        mode = self.hf_service_mode.strip().lower()
        if not mode:
            errors.append(f"HF 已开启，但 {primary_hf_env_name('service_mode')} 未设置")
            return errors

        required_by_mode = {
            "collect": ["collect_dataset", "collect_symbols"],
            "backfill": ["backfill_dataset", "backfill_symbols", "backfill_start_date", "backfill_end_date"],
            "repair": ["repair_dataset"],
        }
        if mode not in required_by_mode:
            errors.append(f"{primary_hf_env_name('service_mode')}={mode} 不支持（只允许 collect|backfill|repair）")
            return errors

        for key in required_by_mode[mode]:
            if not hf_env(key):
                errors.append(f"HF 模式缺少必填环境变量：{primary_hf_env_name(key)}")

        dataset_cli = hf_env(f"{mode}_dataset")
        if dataset_cli:
            spec = find_dataset_by_cli(dataset_cli, mode)
            if spec is None:
                errors.append(f"HF {mode} 数据集未注册：{dataset_cli}")
            elif not spec.runtime_status == "reserved" and spec.dataset_key not in self.hf_enabled_dataset_keys:
                errors.append(f"HF {mode} 数据集未启用：{spec.dataset_key}（需设置 {spec.enabled_env}=1）")
        if errors:
            return errors
        return self.validate_hf_canary(require_hf_enabled=False)

    def validate_hf_canary(self, *, require_hf_enabled: bool = True) -> list[str]:
        errors: list[str] = []

        if not self.enable_hf:
            if require_hf_enabled:
                errors.append("HF canary 未开启（需设置 BINANCE_STACK_ENABLE_HF=1）")
            return errors

        enabled = self.hf_enabled_dataset_keys
        if len(enabled) != 1:
            errors.append(f"HF canary 只允许启用 1 个 dataset（当前 {len(enabled)} 个）")
            return errors

        mode = self.hf_service_mode.strip().lower()
        if mode not in {"collect", "backfill", "repair"}:
            errors.append(f"HF canary 模式非法：{mode or '-'}")
            return errors

        dataset_cli = hf_env(f"{mode}_dataset")
        spec = find_dataset_by_cli(dataset_cli, mode) if dataset_cli else None
        if spec is None:
            errors.append(f"HF canary dataset 未注册：{dataset_cli or '-'}")
            return errors
        if spec.dataset_key != enabled[0]:
            errors.append(f"HF canary dataset 与启用开关不一致：enabled={enabled[0]} cli={spec.dataset_key}")

        raw_symbols = hf_env(f"{mode}_symbols")
        symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
        if len(symbols) != 1:
            errors.append(f"HF canary 只允许单 symbol smoke（当前 {len(symbols)} 个）")

        if not hf_env("database_url"):
            errors.append(f"HF canary 缺少数据库 DSN：{primary_hf_env_name('database_url')}")

        try:
            smoke_seconds = int(hf_env("canary_smoke_seconds", "120") or "120")
        except ValueError:
            errors.append(f"{primary_hf_env_name('canary_smoke_seconds')} 必须是整数秒")
        else:
            if smoke_seconds <= 0:
                errors.append(f"{primary_hf_env_name('canary_smoke_seconds')} 必须大于 0")
        return errors


def load_stack_config() -> BinanceStackConfig:
    var_root = LAYOUT.stack_root / "var"
    return BinanceStackConfig(
        project_root=LAYOUT.project_root,
        stack_root=LAYOUT.stack_root,
        env_file=LAYOUT.env_file,
        var_root=var_root,
        lf_runtime_dir=var_root / "lf",
        hf_runtime_dir=var_root / "hf",
        enable_lf=bool_env("BINANCE_STACK_ENABLE_LF", default=True),
        enable_hf=bool_env("BINANCE_STACK_ENABLE_HF", default=False),
        dry_run=bool_env("BINANCE_STACK_DRY_RUN", default=False),
        hf_service_mode=hf_env("service_mode"),
    )
