"""官方历史归档源 ZIP 下载/校验工具（CHECKSUM + 结构校验）。

目标：
- 复用 trades backfill 的“强一致性”下载逻辑，避免每个数据集重复造轮子。
- 只做：ZIP 下载 + 校验（是否包含 CSV、sha256 对齐）。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Optional
import zipfile

from binance.sources.archive_source.download_utils import (
    download_file,
    download_text,
    parse_checksum_text,
    probe_content_length,
    sha256_file,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerifiedZipResult:
    ok: bool
    status_code: Optional[int]
    error: Optional[str]
    checksum_sha256: Optional[str]
    verified: bool


def _checksum_url(zip_url: str) -> str:
    return f"{zip_url}.CHECKSUM"


def zip_has_csv(zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return any(n.lower().endswith(".csv") for n in zf.namelist())
    except Exception:
        return False


def download_or_repair_zip(
    url: str,
    dst: Path,
    *,
    allow_no_checksum: bool,
    timeout_seconds: float = 60.0,
    max_retries: int = 3,
) -> VerifiedZipResult:
    zip_filename = dst.name
    checksum_url = _checksum_url(url)

    checksum_resp = download_text(checksum_url, timeout_seconds=timeout_seconds, max_retries=max_retries)
    expected_sha: Optional[str] = None
    verified = False

    if checksum_resp.ok:
        mapping = parse_checksum_text(checksum_resp.text or "")
        expected_sha = mapping.get(zip_filename)
        if not expected_sha:
            return VerifiedZipResult(
                ok=False,
                status_code=int(checksum_resp.status_code or 0) or None,
                error=f"CHECKSUM 解析失败: 未找到 {zip_filename}",
                checksum_sha256=None,
                verified=False,
            )
        verified = True
    else:
        if int(checksum_resp.status_code or 0) == 404:
            if not allow_no_checksum:
                return VerifiedZipResult(
                    ok=False,
                    status_code=404,
                    error="CHECKSUM 404（严格模式禁止继续）",
                    checksum_sha256=None,
                    verified=False,
                )
            logger.warning("CHECKSUM 404（逃生模式允许继续，但会标记为 unverified）: %s", checksum_url)
        else:
            if not allow_no_checksum:
                return VerifiedZipResult(
                    ok=False,
                    status_code=checksum_resp.status_code,
                    error=f"CHECKSUM 下载失败（严格模式禁止继续）: {checksum_resp.error}",
                    checksum_sha256=None,
                    verified=False,
                )
            logger.warning("CHECKSUM 下载失败（逃生模式允许继续，但会标记为 unverified）: %s", checksum_resp.error)

    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists():
        if zip_has_csv(dst):
            if expected_sha:
                local_sha = sha256_file(dst)
                if local_sha == expected_sha:
                    return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=expected_sha, verified=True)
                logger.warning("发现 sha256 不一致 ZIP，准备重下: %s", dst)
            else:
                remote_size = probe_content_length(url, timeout_seconds=timeout_seconds)
                if remote_size is None:
                    local_sha = sha256_file(dst)
                    return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=local_sha, verified=False)
                local_size = dst.stat().st_size
                if local_size == remote_size:
                    local_sha = sha256_file(dst)
                    return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=local_sha, verified=False)
                logger.warning("发现大小不一致 ZIP，准备重下: %s (local=%d remote=%d)", dst, local_size, remote_size)
        else:
            logger.warning("发现损坏 ZIP，准备重下: %s", dst)

        try:
            dst.unlink()
        except Exception as e:
            return VerifiedZipResult(ok=False, status_code=None, error=f"删除损坏文件失败: {e}", checksum_sha256=None, verified=False)

    for attempt in range(max_retries):
        r = download_file(url, dst, timeout_seconds=timeout_seconds, max_retries=max_retries)
        if not r.ok:
            return VerifiedZipResult(ok=False, status_code=r.status_code, error=r.error, checksum_sha256=None, verified=False)

        if not zip_has_csv(dst):
            try:
                dst.unlink()
            except Exception:
                pass
            return VerifiedZipResult(ok=False, status_code=None, error="下载后 ZIP 校验失败（无 CSV 或文件损坏）", checksum_sha256=None, verified=False)

        local_sha = sha256_file(dst)
        if expected_sha and local_sha != expected_sha:
            logger.warning("sha256 校验失败，准备重下: %s (attempt=%d/%d)", dst, attempt + 1, max_retries)
            try:
                dst.unlink()
            except Exception:
                pass
            continue

        return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=expected_sha or local_sha, verified=verified)

    return VerifiedZipResult(ok=False, status_code=None, error="sha256 校验失败且重试耗尽", checksum_sha256=None, verified=False)


__all__ = ["VerifiedZipResult", "download_or_repair_zip", "zip_has_csv"]

