from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from src.utils.hash import sha256_text
from src.utils.redact import redact_text


@dataclass(frozen=True)
class RunAssetPaths:
    root: Path
    context_json: Path
    context_md: Path
    prompt_txt: Path
    prompt_hash: Path
    context_hash: Path
    llm_response_raw: Path
    decision_json: Path
    services_dir: Path


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        # 只做 best-effort：权限不足时不影响主流程
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _atomic_write_json(path: Path, obj: object) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    _atomic_write_text(path, text + "\n")


def init_run_asset(run_assets_dir: Path, trace_id: str) -> RunAssetPaths:
    run_assets_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_assets_dir.chmod(0o700)
    except OSError:
        pass
    root = run_assets_dir / trace_id
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return RunAssetPaths(
        root=root,
        context_json=root / "context.json",
        context_md=root / "context.md",
        prompt_txt=root / "prompt.txt",
        prompt_hash=root / "prompt_hash.txt",
        context_hash=root / "context_hash.txt",
        llm_response_raw=root / "llm_response_raw.txt",
        decision_json=root / "decision.json",
        services_dir=root / "services",
    )


def write_context(paths: RunAssetPaths, context: dict, context_md: str) -> str:
    _atomic_write_json(paths.context_json, context)
    _atomic_write_text(paths.context_md, redact_text(context_md))
    context_hash = sha256_text(json.dumps(context, ensure_ascii=False, sort_keys=True))
    _atomic_write_text(paths.context_hash, context_hash + "\n")
    return context_hash


def write_prompt(paths: RunAssetPaths, prompt_text: str) -> str:
    _atomic_write_text(paths.prompt_txt, redact_text(prompt_text))
    prompt_hash = sha256_text(prompt_text)
    _atomic_write_text(paths.prompt_hash, prompt_hash + "\n")
    return prompt_hash


def write_llm_response(paths: RunAssetPaths, raw: str) -> None:
    _atomic_write_text(paths.llm_response_raw, redact_text(raw))


def write_decision(paths: RunAssetPaths, decisions: list[dict]) -> None:
    _atomic_write_json(paths.decision_json, decisions)


_re_safe_name = re.compile(r"[^a-z0-9_]+")


def _safe_name(raw: str, *, default: str) -> str:
    name = (raw or "").strip().lower()
    name = _re_safe_name.sub("_", name)
    name = name.strip("_")
    return name or default


def write_service_asset(paths: RunAssetPaths, *, service: str, payload: object) -> Path:
    """Write *one* JSON per service under run_asset/<trace_id>/services/.

    File layout:
    - services/<service>.json
    """
    service_name = _safe_name(service, default="service")
    out_path = paths.services_dir / f"{service_name}.json"
    _atomic_write_json(out_path, payload)
    return out_path


def write_service_dataset(paths: RunAssetPaths, *, service: str, dataset: str, payload: object) -> Path:
    """Write a service-scoped dataset JSON file under run_asset/<trace_id>/services/.

    File layout:
    - services/<service>/<dataset_path>.json
    """
    service_name = _safe_name(service, default="service")

    # Allow dataset paths like "cards/volume_ranking" (nested dirs), while keeping it safe.
    raw = (dataset or "").strip().replace("\\", "/")
    parts = [p for p in raw.split("/") if p.strip()]
    if not parts:
        parts = [raw or "dataset"]
    safe_parts: list[str] = []
    for i, part in enumerate(parts):
        safe_parts.append(_safe_name(part, default=("dataset" if i == 0 else f"part_{i}")))

    out_dir = paths.services_dir / service_name
    if len(safe_parts) > 1:
        out_dir = out_dir.joinpath(*safe_parts[:-1])
    out_path = out_dir / f"{safe_parts[-1]}.json"
    _atomic_write_json(out_path, payload)
    return out_path
