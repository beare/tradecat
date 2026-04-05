"""LF 统一守护进程。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import signal
import subprocess
import time

from binance.registry import enabled_dataset_keys
from binance.runtime.process_utils import build_runtime_env, choose_python_bin, unlink_if_exists, write_pid


logger = logging.getLogger("binance.runtime.lf_worker")

STACK_ROOT = Path(__file__).resolve().parents[3]
LF_RUNTIME_DIR = STACK_ROOT / "var" / "lf"
RUN_DIR = LF_RUNTIME_DIR / "pids"
LOG_DIR = LF_RUNTIME_DIR / "logs"
DAEMON_PID = RUN_DIR / "daemon.pid"


@dataclass(slots=True)
class ComponentProcess:
    name: str
    command: list[str]
    pid_file: Path
    log_file: Path
    proc: subprocess.Popen[str] | None = None
    restarts: int = 0


class Supervisor:
    def __init__(self) -> None:
        python_bin = choose_python_bin(LF_RUNTIME_DIR, stack_root=STACK_ROOT)
        self._env = build_runtime_env(STACK_ROOT / "src")
        self._running = False
        enabled = set(enabled_dataset_keys(group="lf"))
        components: dict[str, ComponentProcess] = {}
        if "futures_um_candles_1m" in enabled:
            components["ws"] = ComponentProcess(
                name="ws",
                command=[python_bin, "-u", "-m", "binance.runtime.lf_ws_worker"],
                pid_file=RUN_DIR / "ws.pid",
                log_file=LOG_DIR / "ws.log",
            )
        if "futures_um_metrics_snapshot_5m" in enabled:
            components["metrics"] = ComponentProcess(
                name="metrics",
                command=[python_bin, "-u", "-m", "binance.runtime.lf_metrics_worker"],
                pid_file=RUN_DIR / "metrics.pid",
                log_file=LOG_DIR / "metrics.log",
            )
        if {"futures_um_candles_1m", "futures_um_metrics_snapshot_5m"} & enabled:
            components["backfill"] = ComponentProcess(
                name="backfill",
                command=[python_bin, "-u", "-m", "binance.runtime.lf_backfill_worker"],
                pid_file=RUN_DIR / "backfill.pid",
                log_file=LOG_DIR / "backfill.log",
            )
        self._components = components

    def _start_component(self, component: ComponentProcess) -> None:
        component.log_file.parent.mkdir(parents=True, exist_ok=True)
        component.pid_file.parent.mkdir(parents=True, exist_ok=True)
        with component.log_file.open("a", encoding="utf-8") as handle:
            component.proc = subprocess.Popen(
                component.command,
                cwd=str(STACK_ROOT),
                env=self._env,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        write_pid(component.pid_file, int(component.proc.pid))
        logger.info("启动 %s (PID=%d)", component.name, component.proc.pid)

    def _stop_component(self, component: ComponentProcess) -> None:
        proc = component.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        elif component.pid_file.exists():
            try:
                pid = int(component.pid_file.read_text(encoding="utf-8").strip())
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        unlink_if_exists(component.pid_file)
        component.proc = None

    def run(self) -> None:
        if not self._components:
            logger.info("LF 没有启用的数据集，统一守护进程直接退出")
            return

        self._running = True
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        write_pid(DAEMON_PID, os.getpid())
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "_running", False))

        for component in self._components.values():
            self._start_component(component)

        try:
            while self._running:
                for component in self._components.values():
                    proc = component.proc
                    if proc is not None and proc.poll() is not None:
                        if component.restarts >= 10:
                            logger.error("%s 退出且已超过最大重启次数", component.name)
                            self._running = False
                            break
                        component.restarts += 1
                        logger.warning("%s 退出，准备第 %d 次重启", component.name, component.restarts)
                        time.sleep(min(5 * component.restarts, 60))
                        self._start_component(component)
                time.sleep(5)
        finally:
            for component in self._components.values():
                self._stop_component(component)
            unlink_if_exists(DAEMON_PID)
            logger.info("LF unified daemon 已退出")



def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    Supervisor().run()


if __name__ == "__main__":
    main()
