from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "dev-token")
    monkeypatch.setenv("LLM_GATEWAY_RUN_ASSETS_DIR", str(tmp_path / "run_asset"))
    # 禁用真实上游（缺省即降级 wait）
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_MODEL", raising=False)

    from src import config as config_mod

    # Settings 使用 lru_cache；测试里每个用例 run_asset 路径不同，需清缓存避免串扰
    config_mod.get_settings.cache_clear()

    from src.app import app

    return TestClient(app)


@pytest.fixture()
def client_small_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "dev-token")
    monkeypatch.setenv("LLM_GATEWAY_MAX_BODY_BYTES", "200")
    monkeypatch.setenv("LLM_GATEWAY_RUN_ASSETS_DIR", str(tmp_path / "run_asset"))
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_MODEL", raising=False)

    from src import config as config_mod

    config_mod.get_settings.cache_clear()

    from src.app import app

    return TestClient(app)


@pytest.fixture()
def client_no_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "dev-token")
    monkeypatch.setenv("LLM_GATEWAY_RUN_ASSETS_ENABLED", "false")
    monkeypatch.setenv("LLM_GATEWAY_RUN_ASSETS_DIR", str(tmp_path / "run_asset"))
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_UPSTREAM_MODEL", raising=False)

    from src import config as config_mod

    config_mod.get_settings.cache_clear()

    from src.app import app

    return TestClient(app)


def test_healthz(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_unauthorized(client: TestClient):
    r = client.post("/v1/chat/completions", json={"model": "x", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_chat_fallback_wait_and_run_asset(client: TestClient, tmp_path: Path):
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer dev-token"},
        json={
            "model": "any",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "user"},
            ],
            "temperature": 0.0,
            "max_tokens": 16,
        },
    )
    assert r.status_code == 200
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    assert "<decision>" in content
    assert "\"action\":\"wait\"" in content or "\"action\": \"wait\"" in content

    # run_asset 目录应有产物
    run_root = tmp_path / "run_asset"
    assert run_root.exists()
    trace_dirs = [p for p in run_root.iterdir() if p.is_dir()]
    assert trace_dirs, "expected at least one trace dir"
    one = trace_dirs[0]
    assert (one / "context.json").exists()
    assert (one / "prompt.txt").exists()
    assert (one / "decision.json").exists()


def test_body_too_large_returns_wait_without_run_asset(client_small_body: TestClient, tmp_path: Path):
    big = "x" * 1000
    r = client_small_body.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer dev-token"},
        json={"model": "any", "messages": [{"role": "user", "content": big}]},
    )
    assert r.status_code == 200
    content = r.json()["choices"][0]["message"]["content"]
    assert "\"action\":\"wait\"" in content or "\"action\": \"wait\"" in content

    run_root = tmp_path / "run_asset"
    assert not run_root.exists(), "body-too-large should not write run_asset by default"


def test_run_assets_disabled_skips_disk_write(client_no_assets: TestClient, tmp_path: Path):
    r = client_no_assets.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer dev-token"},
        json={"model": "any", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    run_root = tmp_path / "run_asset"
    assert not run_root.exists()


def test_gateway_sanitizes_tilde_and_thousand_sep_in_decision_json(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from src.clients.upstream_openai import UpstreamResult
    from src import gateway as gateway_mod

    async def fake_call_openai_compatible(**_kwargs):
        upstream_content = (
            "<reasoning>r</reasoning>"
            "<decision>"
            '[{"symbol":"ALL","action":"wait","reasoning":"risk ~ 3 and 1,234"}]'
            "</decision>"
        )
        return UpstreamResult(ok=True, content=upstream_content, raw_json={"choices": [{"message": {"content": upstream_content}}]})

    monkeypatch.setattr(gateway_mod, "call_openai_compatible", fake_call_openai_compatible)

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer dev-token"},
        json={
            "model": "any",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "user"},
            ],
        },
    )
    assert r.status_code == 200

    content = r.json()["choices"][0]["message"]["content"]
    assert "<decision>" in content
    assert "~" not in content
    assert "1,234" not in content
