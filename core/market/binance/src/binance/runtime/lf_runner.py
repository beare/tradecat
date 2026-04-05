"""LF 统一运行器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
class LFRunner:
    runtime_dir: Path
    stack_root: Path
    enabled_dataset_keys: tuple[str, ...]

    @property
    def daemon_pid_file(self) -> Path:
        return self.runtime_dir / "pids" / "daemon.pid"

    @property
    def daemon_log_file(self) -> Path:
        return self.runtime_dir / "logs" / "daemon.log"

    @property
    def component_names(self) -> tuple[str, ...]:
        names: list[str] = []
        if "futures_um_candles_1m" in self.enabled_dataset_keys:
            names.append("ws")
        if "futures_um_metrics_snapshot_5m" in self.enabled_dataset_keys:
            names.append("metrics")
        if {"futures_um_candles_1m", "futures_um_metrics_snapshot_5m"} & set(self.enabled_dataset_keys):
            names.append("backfill")
        return tuple(names)

    def _component_pid_file(self, name: str) -> Path:
        return self.runtime_dir / "pids" / f"{name}.pid"

    def _python_bin(self) -> str:
        return choose_python_bin(self.runtime_dir, stack_root=self.stack_root)

    def _env(self) -> dict[str, str]:
        return build_runtime_env(self.stack_root / "src")

    def _command(self) -> list[str]:
        return [self._python_bin(), "-u", "-m", "binance.runtime.lf_worker"]

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
        raise RuntimeError(f"不支持的 LF action: {action}")

    def start(self, *, dry_run: bool) -> int:
        if not self.component_names:
            print("  [LF] 没有启用的数据集，跳过启动")
            return 0
        pid = read_pid(self.daemon_pid_file)
        if is_running(pid):
            print(f"  [LF] daemon 已运行 (PID: {pid})")
            return 0

        unlink_if_exists(self.daemon_pid_file)
        if dry_run:
            print(f"  [LF] (dry-run) datasets={','.join(self.enabled_dataset_keys)}")
            print(f"  [LF] (dry-run) (cd {self.stack_root} && {' '.join(self._command())})")
            return 0

        pid = start_detached(
            command=self._command(),
            cwd=self.stack_root,
            env=self._env(),
            log_file=self.daemon_log_file,
        )
        write_pid(self.daemon_pid_file, pid)
        import time

        time.sleep(1)
        if is_running(pid):
            print(f"  [LF] daemon 已启动 (PID: {pid})")
            print(f"  [LF] 数据集: {','.join(self.enabled_dataset_keys)}")
            print(f"  [LF] 日志: {self.daemon_log_file}")
            return 0
        print("  [LF] daemon 启动失败")
        unlink_if_exists(self.daemon_pid_file)
        return 1

    def stop(self, *, dry_run: bool) -> int:
        if dry_run:
            print(f"  [LF] (dry-run) stop {self.daemon_pid_file}")
            return 0

        pid = read_pid(self.daemon_pid_file)
        if is_running(pid):
            stop_process(pid)
        unlink_if_exists(self.daemon_pid_file)

        for name in ("backfill", "metrics", "ws"):
            child_pid_file = self._component_pid_file(name)
            child_pid = read_pid(child_pid_file)
            if is_running(child_pid):
                stop_process(child_pid)
            unlink_if_exists(child_pid_file)
        print("  [LF] 已停止")
        return 0

    def status(self, *, dry_run: bool) -> int:
        if dry_run:
            print("  [LF] (dry-run) status")
            return 0

        if not self.component_names:
            print("  [LF] 没有启用的数据集")
            return 0

        rc = 0
        daemon_pid = read_pid(self.daemon_pid_file)
        if is_running(daemon_pid):
            print(f"  [LF] ✓ daemon: 运行中 (PID: {daemon_pid}, 运行: {format_uptime(daemon_pid)})")
        else:
            print("  [LF] ✗ daemon: 未运行")
            rc = 1

        active_components = set(self.component_names)
        for name in ("ws", "metrics", "backfill"):
            if name not in active_components:
                print(f"  [LF] - {name}: skipped")
                continue
            pid = read_pid(self._component_pid_file(name))
            if is_running(pid):
                print(f"  [LF] ✓ {name}: 运行中 (PID: {pid}, 运行: {format_uptime(pid)})")
            else:
                print(f"  [LF] ✗ {name}: 未运行")
                rc = 1
        return rc



def build_lf_runner(config: BinanceStackConfig) -> LFRunner:
    return LFRunner(
        runtime_dir=config.lf_runtime_dir,
        stack_root=config.stack_root,
        enabled_dataset_keys=config.lf_enabled_dataset_keys,
    )
