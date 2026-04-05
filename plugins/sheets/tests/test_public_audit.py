from __future__ import annotations

from src.public_audit import audit_public_tab_names, audit_sensitive_text_cells
from src.public_meta import redact_abs_paths_text


def test_audit_public_tab_names_rejects_random_sheet_names() -> None:
    bad = audit_public_tab_names(titles=["Sheet1", "工作表6", "加密货币看板", "API", ""])
    assert "Sheet1" in bad
    assert "工作表6" in bad
    assert "加密货币看板" not in bad
    assert "API" not in bad


def test_redact_abs_paths_text_masks_home_paths() -> None:
    s = "failed to open /home/lenovo/.config/gcp/credentials/key.json"
    out = redact_abs_paths_text(s)
    assert "/home/lenovo" not in out
    assert "<path:key.json>" in out

    s2 = r"open C:\Users\lenovo\.ssh\id_rsa failed"
    out2 = redact_abs_paths_text(s2)
    assert r"C:\Users\lenovo" not in out2
    assert "<path:id_rsa>" in out2

    s3 = r"missing \\wsl.localhost\Ubuntu\home\lenovo\.config\gcp\credentials\key.json"
    out3 = redact_abs_paths_text(s3)
    assert r"\\wsl.localhost\Ubuntu\home\lenovo" not in out3
    assert "<path:key.json>" in out3


def test_audit_sensitive_text_cells_rules_and_masking() -> None:
    values = [
        ["postgresql://user:pass@10.0.0.1:5432/db", "token=" + ("a" * 40)],
        ["Bearer " + ("b" * 40), "connect 192.168.1.2 ok"],
        ["see /home/lenovo/.ssh/id_rsa", r"missing \\wsl.localhost\Ubuntu\home\lenovo\.config\gcp\credentials\key.json"],
        [r"open C:\Users\lenovo\.ssh\id_rsa failed", "public text"],
    ]
    hits = audit_sensitive_text_cells(sheet_title="API", a1_range="A1:B4", values=values)
    rules = {h.rule for h in hits}
    assert {"dsn", "secret_kv", "bearer_token", "private_ip", "abs_path"} <= rules

    # excerpt must be masked: no raw IP/token/path should leak to stdout/logs
    for h in hits:
        assert "10.0.0.1" not in h.excerpt
        assert "192.168.1.2" not in h.excerpt
        assert "/home/lenovo" not in h.excerpt
        assert r"\\wsl.localhost\Ubuntu\home\lenovo" not in h.excerpt
        assert r"C:\Users\lenovo" not in h.excerpt
        assert ("a" * 28) not in h.excerpt
        assert ("b" * 28) not in h.excerpt


def test_audit_sensitive_text_cells_cell_coordinates_respect_range_start() -> None:
    hits = audit_sensitive_text_cells(
        sheet_title="S",
        a1_range="B2:C2",
        values=[["postgresql://user:pass@127.0.0.1:5432/db"]],
    )
    assert hits
    assert hits[0].cell == "B2"
