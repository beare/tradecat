from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.__main__ import _validate_write_target
from src.config import Settings
from src.sa_sheets_writer import _col_to_index, _flatten_eav


def test_col_to_index() -> None:
    assert _col_to_index("A") == 1
    assert _col_to_index("Z") == 26
    assert _col_to_index("AA") == 27
    assert _col_to_index("AZ") == 52
    assert _col_to_index("BA") == 53


def test_col_to_index_invalid() -> None:
    with pytest.raises(ValueError):
        _col_to_index("")
    with pytest.raises(ValueError):
        _col_to_index("A1")


def test_flatten_eav_scalar() -> None:
    assert list(_flatten_eav("x", 1)) == [("x", "number", "1")]
    assert list(_flatten_eav("x", True)) == [("x", "bool", "True")]
    assert list(_flatten_eav("x", None)) == [("x", "null", "")]


def test_flatten_eav_object_and_array() -> None:
    obj = {"a": 1, "b": {"c": 2}, "arr": [10, {"k": "v"}]}
    out = list(_flatten_eav("", obj))

    # container first
    assert out[0] == ("_", "object", "")

    # key paths exist
    assert ("a", "number", "1") in out
    assert ("b", "object", "") in out
    assert ("b.c", "number", "2") in out
    assert ("arr", "array", "") in out
    assert ("arr[0]", "number", "10") in out
    assert ("arr[1]", "object", "") in out
    assert ("arr[1].k", "string", "v") in out


def test_settings_default_dashboard_col_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHEETS_DASHBOARD_COL_L", raising=False)
    monkeypatch.delenv("SHEETS_DASHBOARD_COL_R", raising=False)
    monkeypatch.delenv("SHEETS_DASHBOARD_MODE", raising=False)
    monkeypatch.delenv("SHEETS_DASHBOARD_SLOT_HEIGHT", raising=False)
    monkeypatch.delenv("SHEETS_EXPORT_MULTI_PERIODS", raising=False)
    settings = Settings.from_env(tmp_path)
    assert settings.dashboard_col_l == "A"
    assert settings.dashboard_col_r == "BS"
    assert settings.dashboard_mode == "replace"
    assert settings.dashboard_slot_height == 10


def test_validate_write_target_allows_dry_run_without_transport(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(tmp_path),
        write_mode="webhook",
        webhook_url="",
        webhook_secret="",
        dry_run=True,
    )
    assert _validate_write_target(settings) is None


def test_validate_write_target_requires_webhook_config_when_not_dry_run(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(tmp_path),
        write_mode="webhook",
        webhook_url="",
        webhook_secret="",
        dry_run=False,
    )
    message = _validate_write_target(settings)
    assert message is not None
    assert "SHEETS_WEBHOOK_URL" in message
