from __future__ import annotations

import re
from typing import Any


def _fmt_float(x: float) -> str:
    # 尽量稳定：避免科学计数法；最多 3 位小数，去掉尾随 0
    s = f"{float(x):.3f}"
    s = s.rstrip("0").rstrip(".")
    return s or "0"


def _fmt_key(k: str) -> str:
    s = str(k or "").strip()
    if not s:
        return "_"
    # key 不允许包含分隔符，否则会破坏“中文逗号 kv”的可解析性
    s = s.replace("，", " ").replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or "_"


_ABS_PATH_RE = re.compile(r"/(?:home|root|Users|var|opt|etc|tmp|mnt|srv|data)(?:/[^\s]+)+")
_WIN_ABS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s]+")
_WSL_UNC_PATH_RE = re.compile(r"\\\\wsl\.localhost\\[^\s]+")


def redact_abs_paths_text(s: str) -> str:
    """
    公开表防泄露兜底：把异常/日志里常见的绝对路径替换为占位符。
    - 只处理常见 Linux 绝对路径前缀（/home,/root,/var,...）
    - 保留 basename，便于定位问题但不暴露目录结构
    """
    x = str(s or "")
    if not x:
        return x

    def _repl(m: re.Match[str]) -> str:
        raw = str(m.group(0) or "")
        raw = raw.rstrip("\\/")  # 兼容 / 与 \ 分隔符
        seg = re.split(r"[\\\\/]", raw)[-1] if raw else ""
        seg = seg.strip().strip(").,;:")
        seg = seg or "path"
        return f"<path:{seg}>"

    x = _ABS_PATH_RE.sub(_repl, x)
    x = _WIN_ABS_PATH_RE.sub(_repl, x)
    x = _WSL_UNC_PATH_RE.sub(_repl, x)
    return x


def _fmt_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _fmt_float(v)

    s = str(v)
    # 元信息行要求“单行单元格”：统一去掉换行，压缩空白
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # 防泄露兜底：绝对路径脱敏（避免把内部路径写入公开表）
    s = redact_abs_paths_text(s)
    if not s:
        return "null"

    # value 里也尽量不放分隔符，避免解析歧义
    s = s.replace("，", " ").replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or "null"


def format_public_meta_row_text(
    *,
    dataset: str,
    exported_at_utc8: str,
    lang: str,
    interval_seconds: float | int | None = None,
    window: str | None = None,
    row_count: int | None = None,
    mode: str | None = None,
    schema: str | None = None,
    extra_kv: list[tuple[str, Any]] | None = None,
) -> str:
    """
    生成公开表统一的“元信息行”（单单元格，中文逗号 key/value 交替）。

    设计目标：
    - 人眼可读 + 机器可解析（split by `，` -> pairwise kv）
    - 避免泄露内部路径/host/token（调用方禁止传入敏感值）
    - 单行单元格（无换行）
    """
    pairs: list[tuple[str, Any]] = [
        ("数据源", dataset),
        ("导出时间(UTC+8)", exported_at_utc8),
        ("刷新间隔(s)", interval_seconds),
        ("窗口", window),
        ("条数", row_count),
        ("语言", lang),
        ("模式", mode),
        ("schema", schema),
    ]
    if extra_kv:
        pairs.extend(list(extra_kv))

    parts: list[str] = []
    for k, v in pairs:
        parts.append(_fmt_key(str(k)))
        parts.append(_fmt_value(v))
    return "，".join(parts)
