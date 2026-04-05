from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.public_meta import redact_abs_paths_text


def _index_to_col(idx: int) -> str:
    n = int(idx)
    if n <= 0:
        raise ValueError(f"invalid_col_index:{idx}")
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _a1_cell(row_1: int, col_1: int) -> str:
    return f"{_index_to_col(int(col_1))}{int(row_1)}"


def _mask_secrets_text(s: str) -> str:
    x = str(s or "")
    if not x:
        return x

    # 绝对路径先脱敏（避免把 /home/... 打到日志里）
    x = redact_abs_paths_text(x)

    # IP：仅保留段位提示
    x = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", x)

    # token/secret：长串统一遮蔽（避免误打到 stdout）
    x = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9\-_\.]{10,}\b", r"\1<secret>", x)
    x = re.sub(r"[A-Za-z0-9\-_]{28,}", "<secret>", x)
    return x


def _excerpt_around(s: str, start: int, end: int, *, width: int = 24) -> str:
    x = str(s or "")
    if not x:
        return ""
    lo = max(int(start) - int(width), 0)
    hi = min(int(end) + int(width), len(x))
    return _mask_secrets_text(x[lo:hi])


def _is_private_ipv4(ip: str) -> bool:
    try:
        parts = [int(p) for p in str(ip).split(".")]
        if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
            return False
    except Exception:
        return False
    a, b, _c, _d = parts
    # 10.0.0.0/8
    if a == 10:
        return True
    # 172.16.0.0/12
    if a == 172 and 16 <= b <= 31:
        return True
    # 192.168.0.0/16
    if a == 192 and b == 168:
        return True
    # 100.64.0.0/10 (CGNAT)
    if a == 100 and 64 <= b <= 127:
        return True
    return False


@dataclass(frozen=True)
class SensitiveHit:
    sheet: str
    cell: str
    rule: str
    excerpt: str


def audit_public_tab_names(*, titles: list[str]) -> list[str]:
    bad: list[str] = []
    for t in titles:
        s = str(t or "").strip()
        if not s:
            bad.append(s)
            continue
        if re.fullmatch(r"(工作表|Sheet)\d+", s, flags=re.IGNORECASE):
            bad.append(s)
            continue
    return bad


def audit_sensitive_text_cells(
    *,
    sheet_title: str,
    a1_range: str,
    values: list[list[Any]],
) -> list[SensitiveHit]:
    """
    输入：某个范围的 values（二维数组），输出命中的敏感信息列表。

    注意：
    - 这里不打印原文，所有 excerpt 都做了脱敏
    - 规则偏“宁可误报也不漏报”（公开表安全第一）
    """
    # 规则：绝对路径 / DSN / token 关键字 / 私网 IP
    abs_path_re = re.compile(r"/(?:home|root|Users|var|opt|etc|tmp|mnt|srv|data)(?:/[^\s]+)+")
    win_abs_path_re = re.compile(r"(?i)\b[A-Z]:\\[^\s]+")
    wsl_unc_path_re = re.compile(r"(?i)\\\\wsl\\.localhost\\[^\s]+")
    dsn_re = re.compile(r"(?i)\b(postgres(?:ql)?|mysql|redis|mongodb)://")
    key_re = re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*\S+")
    bearer_re = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-_.]{10,}\b")
    ip_re = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    hits: list[SensitiveHit] = []

    # 解析起点（例如 A1:C20），用于计算 cell 坐标
    m = re.match(r"^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$", str(a1_range or "").strip().upper())
    start_col_1 = 1
    start_row_1 = 1
    if m:
        c0 = m.group(1) or "A"
        r0 = m.group(2) or "1"
        start_row_1 = int(r0)
        # col -> idx
        col_idx = 0
        for ch in c0:
            col_idx = col_idx * 26 + (ord(ch) - ord("A") + 1)
        start_col_1 = int(col_idx)

    for r_i, row in enumerate(values or []):
        if not isinstance(row, list):
            continue
        for c_i, v in enumerate(row):
            if v is None:
                continue
            s = str(v)
            if not s.strip():
                continue

            cell = _a1_cell(int(start_row_1) + int(r_i), int(start_col_1) + int(c_i))

            def add(rule: str, mo: re.Match[str]) -> None:
                hits.append(
                    SensitiveHit(
                        sheet=str(sheet_title or ""),
                        cell=str(cell),
                        rule=str(rule),
                        excerpt=_excerpt_around(s, int(mo.start()), int(mo.end())),
                    )
                )

            mo = abs_path_re.search(s)
            if mo:
                add("abs_path", mo)
                continue
            mo = win_abs_path_re.search(s)
            if mo:
                add("abs_path", mo)
                continue
            mo = wsl_unc_path_re.search(s)
            if mo:
                add("abs_path", mo)
                continue

            mo = dsn_re.search(s)
            if mo:
                add("dsn", mo)
                continue

            mo = key_re.search(s)
            if mo:
                add("secret_kv", mo)
                continue

            mo = bearer_re.search(s)
            if mo:
                add("bearer_token", mo)
                continue

            for mo2 in ip_re.finditer(s):
                ip = str(mo2.group(0) or "")
                if _is_private_ipv4(ip):
                    add("private_ip", mo2)
                    break

    return hits
