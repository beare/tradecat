from __future__ import annotations

import re


_re_bearer = re.compile(r"(?i)authorization\\s*:\\s*bearer\\s+\\S+")
_re_bearer_any = re.compile(r"(?i)\\bbearer\\s+[A-Za-z0-9._-]{16,}")
_re_internal_token = re.compile(r"(?i)x-internal-token\\s*:\\s*\\S+")
_re_sk = re.compile(r"\\b(sk-[A-Za-z0-9]{8,})\\b")
_re_json_secret = re.compile(r'(?i)("(?:(?:api[_-]?key)|token|secret)"\\s*:\\s*")([^"]+)(")')


def redact_text(s: str) -> str:
    """尽量保守：只做最明显的 token 痕迹脱敏，避免误删业务内容。"""
    if not s:
        return s
    s = _re_bearer.sub("Authorization: Bearer ***", s)
    s = _re_internal_token.sub("X-Internal-Token: ***", s)
    s = _re_bearer_any.sub("Bearer ***", s)
    s = _re_sk.sub("sk-***", s)
    s = _re_json_secret.sub(r"\1***\3", s)
    return s
