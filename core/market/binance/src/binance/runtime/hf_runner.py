"""HF 统一运行器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil

from binance.common.hf_env import hf_env
from binance.config import BinanceStackConfig
from binance.runtime.process_utils import (
    build_runtime_env,
    choose_python_bin,
    format_uptime,
    is_running,
    read_pid,
    start_detached,
    stop_process,
    unlink_if_exists,
    write_pid,
)


@dataclass(frozen=True, slots=True)
class HFRunner:
    runtime_dir: Path
    stack_root: Path
    enabled_dataset_keys: tuple[str, ...]

    @property
    def pid_file(self) -> Path:
        return self.runtime_dir / "pids" / "service.pid"

    @property
    def log_file(self) -> Path:
        return self.runtime_dir / "logs" / "service.log"

    @property
    def meta_file(self) -> Path:
        return self.runtime_dir / "run" / "service.meta"

    @property
    def evidence_root(self) -> Path:
        raw = (hf_env("canary_evidence_dir") or "").strip()
        if not raw:
            return self.runtime_dir / "evidence"
        path = Path(raw)
        if not path.is_absolute():
            path = self.stack_root / path
        return path

    def _python_bin(self) -> str:
        return choose_python_bin(self.runtime_dir, stack_root=self.stack_root)

    def _env(self) -> dict[str, str]:
        return build_runtime_env(self.stack_root / "src")

    def _command(self) -> list[str]:
        return [self._python_bin(), "-u", "-m", "binance.runtime.hf_worker"]

    def _label(self) -> str:
        mode = hf_env("service_mode")
        if mode == "collect":
            dataset = hf_env("collect_dataset")
            symbols = hf_env("collect_symbols")
            return f"collect dataset={dataset} symbols={symbols}"
        if mode == "backfill":
            dataset = hf_env("backfill_dataset")
            symbols = hf_env("backfill_symbols")
            start_date = hf_env("backfill_start_date")
            end_date = hf_env("backfill_end_date")
            return f"backfill dataset={dataset} symbols={symbols} start={start_date} end={end_date}"
        if mode == "repair":
            dataset = hf_env("repair_dataset")
            symbols = hf_env("repair_symbols") or "all"
            return f"repair dataset={dataset} symbols={symbols}"
        return mode or "unknown"

    def _write_meta(self) -> None:
        self.meta_file.parent.mkdir(parents=True, exist_ok=True)
        mode = hf_env("service_mode")
        dataset = hf_env(f"{mode}_dataset") if mode in {"collect", "backfill", "repair"} else ""
        symbols = hf_env(f"{mode}_symbols") if mode in {"collect", "backfill", "repair"} else ""
        payload = "\n".join(
            [
                f"mode={mode}",
                f"label={self._label()}",
                f"dataset={dataset}",
                f"symbols={symbols}",
                f"enabled_datasets={','.join(self.enabled_dataset_keys)}",
                f"canary_smoke_seconds={hf_env('canary_smoke_seconds', '120') or '120'}",
                f"evidence_root={self.evidence_root}",
                f"started_at_utc={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            ]
        )
        self.meta_file.write_text(payload + "\n", encoding="utf-8")

    def archive_evidence(self, reason: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.evidence_root / f"{stamp}-{reason}"
        target.mkdir(parents=True, exist_ok=True)
        for source in (self.log_file, self.meta_file, self.pid_file):
            if source.exists():
                shutil.copy2(source, target / source.name)
        summary = "\n".join(
            [
                f"reason={reason}",
                f"mode={hf_env('service_mode') or '-'}",
                f"label={self._label()}",
                f"enabled_datasets={','.join(self.enabled_dataset_keys) or '-'}",
                f"archived_at_utc={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            ]
        )
        (target / "summary.txt").write_text(summary + "\n", encoding="utf-8")
        return target

    def run(self, action: str, *, dry_run: bool = False) -> int:
        if action == "start":
            return self.start(dry_run=dry_run)
        if action == "stop":
            return self.stop(dry_run=dry_run)
        if action == "status":
            return self.status(dry_run=dry_run)
        if action == "restart":
            rc = self.stop(dry_run=dry_run)
            rc = max(rc, self.start(dry_run=dry_run))
            return rc
        raise RuntimeError(f"不支持的 HF action: {action}")

    def start(self, *, dry_run: bool) -> int:
        if not self.enabled_dataset_keys:
            print("  [HF] 没有启用的数据集，跳过启动")
            return 0
        pid = read_pid(self.pid_file)
        if is_running(pid):
            print(f"  [HF] 服务已运行 (PID: {pid})")
            return 0

        unlink_if_exists(self.pid_file)
        if dry_run:
            print(f"  [HF] (dry-run) datasets={','.join(self.enabled_dataset_keys)}")
            print(f"  [HF] (dry-run) (cd {self.stack_root} && {' '.join(self._command())})")
            return 0

        pid = start_detached(
            command=self._command(),
            cwd=self.stack_root,
            env=self._env(),
            log_file=self.log_file,
        )
        write_pid(self.pid_file, pid)
        self._write_meta()
        import time

        time.sleep(1)
        if is_running(pid):
            print(f"  [HF] 服务已启动 (PID: {pid})")
            print(f"  [HF] 模式: {hf_env('service_mode')}")
            print(f"  [HF] 数据集: {','.join(self.enabled_dataset_keys)}")
            print(f"  [HF] 日志: {self.log_file}")
            return 0
        print("  [HF] 服务启动失败")
        unlink_if_exists(self.pid_file)
        return 1

    def stop(self, *, dry_run: bool) -> int:
        if dry_run:
            print(f"  [HF] (dry-run) stop {self.pid_file}")
            return 0

        pid = read_pid(self.pid_file)
        if is_running(pid):
            stop_process(pid)
        unlink_if_exists(self.pid_file)
        print("  [HF] 已停止")
        return 0

    def status(self, *, dry_run: bool) -> int:
        if dry_run:
            print("  [HF] (dry-run) status")
            return 0

        if not self.enabled_dataset_keys:
            print("  [HF] 没有启用的数据集")
            return 0

        pid = read_pid(self.pid_file)
        if is_running(pid):
            print(f"  [HF] ✓ 服务运行中 (PID: {pid}, 运行: {format_uptime(pid)})")
            if self.meta_file.exists():
                print(f"  [HF] meta: {self.meta_file}")
            print(f"  [HF] 日志: {self.log_file}")
            return 0
        print("  [HF] ✗ 服务未运行")
        return 1



def build_hf_runner(config: BinanceStackConfig) -> HFRunner:
    return HFRunner(
        runtime_dir=config.hf_runtime_dir,
        stack_root=config.stack_root,
        enabled_dataset_keys=config.hf_enabled_dataset_keys,
    )
