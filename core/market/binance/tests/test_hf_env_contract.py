from binance.common.hf_env import hf_env, primary_hf_env_name



def test_new_hf_env_name_has_priority(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_HF_SERVICE_MODE", "collect")
    assert hf_env("service_mode") == "collect"



def test_primary_hf_env_name_is_stable() -> None:
    assert primary_hf_env_name("service_mode") == "BINANCE_HF_SERVICE_MODE"
