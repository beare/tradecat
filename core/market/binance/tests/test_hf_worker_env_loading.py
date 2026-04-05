import importlib


def test_hf_worker_loads_env_defaults(monkeypatch) -> None:
    monkeypatch.delenv("BINANCE_HF_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import binance.runtime.hf_worker as hf_worker

    importlib.reload(hf_worker)

    assert hf_worker._database_url()
