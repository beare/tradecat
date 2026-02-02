# STATUS - 进度真相源

## State

- Status: Not Started
- Updated: 2026-01-30

## Live Evidence（本任务规划阶段已观察到的事实）

> 说明：以下为规划阶段在仓库内执行的“只读检查”所得证据片段，用于锁定现状与后续 diff 的参照点。

### Evidence 1: 仓库根目录无 `tasks/`（规划前）

- Command: `ls -la tasks`
- Observed: `ls: cannot access 'tasks': No such file or directory`

### Evidence 2: 全局配置模板存在 raw/quality schema 约定

- Source: `config/.env.example#L24-L30`
- Observed (excerpt):
  - `DATABASE_URL=postgresql://postgres:postgres@localhost:5434/market_data`
  - `RAW_DB_SCHEMA=raw`
  - `QUALITY_DB_SCHEMA=quality`

### Evidence 3: WSCollector 产出扩展字段（quote/taker）

- Source: `services-preview/markets-service/src/crypto/collectors/ws.py#L90-L98`
- Observed: row 包含 `quote_volume / trade_count / taker_buy_volume / taker_buy_quote_volume`

### Evidence 4: legacy 表结构（candles_1m）已含 quote/taker 字段

- Source: `libs/database/db/schema/001_timescaledb.sql#L9-L27`
- Observed: `quote_volume / trade_count / taker_buy_*` 列存在

## Checksums（规划产物 SHA256）

- `tasks/INDEX.md`: `8877a84ccd6d3c752aaa1a4baa8d08a2395dc0abdcae067e43408aeb3d4d523c`
- `tasks/0001-single-symbol-fullfield-ingestion/README.md`: `9eee6762f598d88bfb5f51918f05277682fd04d0eb3890cf313b71bdd4a99e30`
- `tasks/0001-single-symbol-fullfield-ingestion/CONTEXT.md`: `ffb68c4c8a7400a0551f9a5361baf2b65736b3971998cf1b4ed2824541eb1fc3`
- `tasks/0001-single-symbol-fullfield-ingestion/ACCEPTANCE.md`: `f5c8ab940ca69d4db0b026c8c548c0d09f2b477f512b96d401dc5e02eed4125d`
- `tasks/0001-single-symbol-fullfield-ingestion/PLAN.md`: `f3a3cdbd414e45a8f644f0095bd6fcdc58dbd365082f70b4022d6b37f2c9d13f`
- `tasks/0001-single-symbol-fullfield-ingestion/TODO.md`: `056c4b75db52108cba054eb61cebdc4e243eafc2fd94e5dc3cc0f0097707faee`
- 注：`STATUS.md` 本身会随执行持续更新，因此不对其自引用写入 SHA（否则每次更新都会失真）。

## Blockers

- Blocked: No

## Next P0

- 按 `TODO.md` 执行建库 + DDL 初始化，然后用 env 注入方式启动 `markets-service crypto-ws`（单币种、raw 模式）。
