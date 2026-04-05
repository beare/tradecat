from __future__ import annotations

import pytest

from src.clients.upstream_openai import call_openai_compatible
from src.utils.net import validate_upstream_url


def test_validate_upstream_url_reject_http_when_require_https():
    err = validate_upstream_url(
        "http://8.8.8.8/v1/chat/completions",
        require_https=True,
        block_private=True,
        allowlist_raw="",
    )
    assert err == "url_scheme_not_https"


def test_validate_upstream_url_reject_loopback_when_block_private():
    err = validate_upstream_url(
        "https://127.0.0.1/v1/chat/completions",
        require_https=True,
        block_private=True,
        allowlist_raw="",
    )
    assert err is not None
    assert err.startswith("ip_not_allowed:")


def test_validate_upstream_url_allow_public_ip_literal():
    err = validate_upstream_url(
        "https://8.8.8.8/v1/chat/completions",
        require_https=True,
        block_private=True,
        allowlist_raw="",
    )
    assert err is None


def test_validate_upstream_url_allowlist_blocks_other_hosts():
    err = validate_upstream_url(
        "https://8.8.8.8/v1/chat/completions",
        require_https=True,
        block_private=True,
        allowlist_raw="1.1.1.1",
    )
    assert err == "url_host_not_in_allowlist"


@pytest.mark.asyncio
async def test_call_openai_compatible_rejects_unsafe_url_before_http_client(monkeypatch: pytest.MonkeyPatch):
    import httpx

    class BombClient:  # pragma: no cover
        def __init__(self, *args, **kwargs):
            raise AssertionError("http client should not be created for unsafe URL")

    monkeypatch.setattr(httpx, "AsyncClient", BombClient)

    r = await call_openai_compatible(
        base_url="http://127.0.0.1:1",
        api_key="sk-test",
        model="m",
        require_https=True,
        block_private=True,
        allowlist_raw="",
        system_prompt="s",
        user_prompt="u",
        temperature=0.0,
        max_tokens=16,
        timeout_seconds=1,
    )
    assert r.ok is False
    assert r.error is not None
    assert r.error.startswith("UNSAFE_UPSTREAM_URL:")

