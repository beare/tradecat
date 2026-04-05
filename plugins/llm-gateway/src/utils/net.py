from __future__ import annotations

import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlsplit


def parse_host_allowlist(raw: str) -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    suffix: set[str] = set()
    for item in (raw or "").split(","):
        s = item.strip().lower().rstrip(".")
        if not s:
            continue
        if s.startswith("."):
            suffix.add(s)
        else:
            exact.add(s)
    return exact, suffix


def is_host_allowed(host: str, exact: set[str], suffix: set[str]) -> bool:
    if not exact and not suffix:
        return True
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    if h in exact:
        return True
    return any(h.endswith(s) for s in suffix)


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


@lru_cache(maxsize=256)
def resolve_host_ips(host: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            ips.add(ipaddress.ip_address(sockaddr[0]))
        elif family == socket.AF_INET6:
            ips.add(ipaddress.ip_address(sockaddr[0]))
    return tuple(sorted(ips, key=lambda x: (x.version, x.compressed)))


def is_ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, block_private: bool) -> bool:
    # 永远拒绝明显异常网段（避免奇怪的多世界）
    if ip.is_unspecified or ip.is_multicast:
        return False
    # 链路本地常用于云厂商元数据服务（SSRF 高危），无论如何都拒绝
    if ip.is_link_local:
        return False
    if ip.is_reserved:
        return False

    if block_private:
        # ip.is_global 会排除：private/loopback/link_local/reserved/multicast/unspecified
        return ip.is_global
    return True


def validate_upstream_url(
    url: str,
    *,
    require_https: bool,
    block_private: bool,
    allowlist_raw: str,
) -> str | None:
    """返回 None 表示安全；否则返回 error_code（用于上层降级）。"""

    u = urlsplit((url or "").strip())
    scheme = (u.scheme or "").lower()
    if not scheme or not u.netloc:
        return "url_missing_scheme_or_host"
    if u.username or u.password:
        return "url_userinfo_not_allowed"

    host = (u.hostname or "").strip().lower().rstrip(".")
    if not host:
        return "url_missing_hostname"

    if require_https and scheme != "https":
        return "url_scheme_not_https"

    exact, suffix = parse_host_allowlist(allowlist_raw)
    if not is_host_allowed(host, exact, suffix):
        return "url_host_not_in_allowlist"

    ip_literal = _parse_ip_literal(host)
    ips: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]
    if ip_literal is not None:
        ips = (ip_literal,)
    else:
        try:
            ips = resolve_host_ips(host)
        except Exception:
            return "dns_resolve_failed"

    for ip in ips:
        if not is_ip_allowed(ip, block_private=block_private):
            return f"ip_not_allowed:{ip.compressed}"

    return None
