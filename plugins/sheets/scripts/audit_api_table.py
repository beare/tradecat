from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from urllib.error import HTTPError


def _env_text(key: str, default: str = "") -> str:
    v = (os.environ.get(key, "") or "").strip()
    return v or default


def _build_gviz_csv_url(*, spreadsheet_id: str, sheet_title: str, tq: str) -> str:
    params = {
        "tqx": "out:csv",
        "sheet": sheet_title,
        "tq": tq,
    }
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?{urllib.parse.urlencode(params)}"


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "tradecat-sheets-service/1.0 (audit_api_table.py)",
            "Accept": "text/csv,*/*",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def _decode_payload(obj: dict) -> tuple[dict, str]:
    data = obj.get("data") or {}
    payload = data.get("payload") or {}
    enc = str(payload.get("encoding") or "")
    if enc != "gzip_base64":
        return payload, ""
    b64 = str(payload.get("gzip_b64") or "")
    raw = gzip.decompress(base64.b64decode(b64))
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _public_check(*, spreadsheet_id: str, api_title: str, limit: int) -> int:
    tq = f"select A,B,C limit {int(limit)}"
    url = _build_gviz_csv_url(spreadsheet_id=spreadsheet_id, sheet_title=api_title, tq=tq)
    try:
        body = _http_get(url).decode("utf-8", errors="replace")
    except HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if code == 404:
            print(
                "❌ gviz/out:csv 返回 404：工作簿可能未设置为“任何人可读”或未发布到 Web（Google 常对私有表返回 404 隐藏存在性）。",
                file=sys.stderr,
            )
            print("   修复：把工作簿共享为“任何人可查看（含链接）”或启用“发布到 Web”。", file=sys.stderr)
            return 2
        print(f"❌ gviz/out:csv HTTPError: {code}", file=sys.stderr)
        return 2

    rows = list(csv.reader(body.splitlines()))
    if not rows:
        print("❌ 读取失败：返回为空", file=sys.stderr)
        return 2

    ok = 0
    bad = 0
    for i, row in enumerate(rows):
        # gviz out:csv 第一行通常是 header
        if i == 0:
            continue
        if not row or not row[0].strip():
            continue
        cell_json = row[0]
        try:
            env = json.loads(cell_json)
            success = bool(env.get("success"))
            decoded, sha = _decode_payload(env)

            if success:
                payload = (env.get("data") or {}).get("payload") or {}
                expect_sha = str(payload.get("raw_sha256") or "")
                integrity = (env.get("data") or {}).get("integrity") or {}
                integrity_sha = str(integrity.get("payload_sha256") or "")

                if sha and expect_sha and sha != expect_sha:
                    raise ValueError("payload_sha256_mismatch")
                if expect_sha and integrity_sha and expect_sha != integrity_sha:
                    raise ValueError("integrity_sha_mismatch")

            schema = str(decoded.get("facts_schema") or "")
            fact_count = int(decoded.get("fact_count") or 0) if isinstance(decoded, dict) else 0
            ok += 1
            print(f"✅ row={i+1} schema={schema or '-'} facts={fact_count}")
        except Exception as exc:
            bad += 1
            print(f"❌ row={i+1} decode_failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    print(f"summary: ok={ok} bad={bad}")
    return 0 if bad == 0 else 3


def main() -> None:
    p = argparse.ArgumentParser(description="Audit public API tab (gviz/out:csv) decode + integrity check")
    p.add_argument("--public-check", action="store_true", help="通过 gviz(out:csv) 验证 API tab 可公开读取 + 解码正确")
    p.add_argument("--spreadsheet-id", default="", help="工作簿 id（默认读 env: SHEETS_SPREADSHEET_ID）")
    p.add_argument("--api-title", default="", help="API tab 名称（默认读 env: SHEETS_API_TAB_TITLE，回退 API）")
    p.add_argument("--limit", type=int, default=20, help="最多检查多少行（默认 20）")
    args = p.parse_args()

    spreadsheet_id = (args.spreadsheet_id or _env_text("SHEETS_SPREADSHEET_ID")).strip()
    api_title = (args.api_title or _env_text("SHEETS_API_TAB_TITLE", "API")).strip() or "API"

    if not args.public_check:
        p.print_help()
        sys.exit(2)

    if not spreadsheet_id:
        print("❌ 缺少 spreadsheet_id：传 --spreadsheet-id 或设置 SHEETS_SPREADSHEET_ID", file=sys.stderr)
        sys.exit(2)

    sys.exit(_public_check(spreadsheet_id=spreadsheet_id, api_title=api_title, limit=int(args.limit or 0)))


if __name__ == "__main__":
    main()
