# CONTEXT - 现状与风险图谱

## 现状追溯（基于仓库可见证据）

- 现有全局配置模板已明确“双 schema 架构”意图：`RAW_DB_SCHEMA=raw`、`QUALITY_DB_SCHEMA=quality`，并默认 TimescaleDB 端口为 5434。见 `config/.env.example#L24-L30`。
- `services-preview/markets-service` 的 crypto 模块支持双写入模式：`CRYPTO_WRITE_MODE=raw` 写入 `raw.*`；`legacy` 写入 `market_data.*`。见 `services-preview/markets-service/src/crypto/config.py#L1-L6`、`services-preview/markets-service/src/crypto/config.py#L83-L90`、`services-preview/markets-service/src/crypto/config.py#L145-L153`。
- `markets-service` 的 WS 采集会产出 K 线扩展字段（`quote_volume / trade_count / taker_buy_*`），并写入 Timescale。见 `services-preview/markets-service/src/crypto/collectors/ws.py#L90-L98`。
- 旧链路（legacy）`market_data.candles_1m` 表结构已包含 quote/taker 字段，但不含 `close_time`。见 `libs/database/db/schema/001_timescaledb.sql#L9-L27`。
- `markets-service` 提供一套完整 DDL（含 raw/quality 等 schema 与 `raw.crypto_kline_1m` 表）。见目录 `services-preview/markets-service/scripts/ddl/`（例如 `03_raw_crypto.sql`、`08_quality.sql`）。

## 约束矩阵（必须遵守）

- **不得修改** `config/.env`（生产配置、含密钥，视为只读）。
- **避免破坏现有链路**：本任务优先选择“新库隔离”，不在既有 `market_data` 上做 DDL 变更。
- **依赖最小化**：优先复用现有 `markets-service`，避免引入新第三方依赖。

## 风险量化表

| 风险点 | 严重程度 | 触发信号 (Signal) | 缓解方案 (Mitigation) |
| :--- | :--- | :--- | :--- |
| `raw/quality` schema 未初始化 | High | 启动采集时报 `relation raw.crypto_kline_1m does not exist` 或 `function quality.start_batch does not exist` | 严格按 `services-preview/markets-service/scripts/ddl/*.sql` 顺序对新库执行；先做 `psql -c "\\dt raw.*"` 预检 |
| 采集范围失控（跑成全市场） | High | 日志出现“加载交易所全部 XXX 个币种”或库内出现非目标币种 | 启动前强制注入 `SYMBOLS_GROUPS=one` + `SYMBOLS_GROUP_one=BTCUSDT`；验收时用 SQL 断言 `COUNT(DISTINCT symbol)=1` |
| 代理被强制指向 `127.0.0.1:9910` | High | 网络请求失败、连接被拒绝 | 现状：`services-preview/markets-service/src/crypto/config.py#L28-L32` 无条件设置代理；若本地无代理，需在实现中改为“尊重现有 env/可关闭” |
| Binance 限流/ban | Medium | 日志出现 429/418 或采集断续 | 先单币种；降低并发；必要时走代理；验收允许短时间 0 写入但必须能自愈重连 |
| 时间戳/时区错误 | Medium | `open_time` 非整分钟或与现实时间偏差大 | 统一使用 UTC；用 SQL 检查 `date_trunc('minute', open_time)=open_time` |
| 重复写入/主键冲突导致丢字段 | Low | `quote_volume/taker*` 长期 NULL 或波动异常 | raw 表使用 `(exchange,symbol,open_time)` upsert；补齐任务用 REST 复写字段 |

## 假设与证伪（每条假设都给出可执行命令）

1. 假设：本机可连接 TimescaleDB（默认 5434）  
   - 证伪：`pg_isready -h localhost -p 5434`
2. 假设：`psql` 可用且具备建库权限  
   - 证伪：`psql -h localhost -p 5434 -U postgres -d postgres -c "SELECT 1"`
3. 假设：`markets-service` 的 DDL 可幂等执行在新库上  
   - 证伪：新库上重复执行 DDL 后 `psql ... -c "\\dn"` 不报错，且表/函数存在
4. 假设：单币种限制可仅通过 env 完成，无需改 `config/.env`  
   - 证伪：`SYMBOLS_GROUPS=one SYMBOLS_GROUP_one=BTCUSDT python -m src crypto-test` 输出写入模式与 schema 正确
5. 假设：WS 采集能拿到 quote/taker 字段（或可由 backfill 补齐）  
   - 证伪：`SELECT quote_volume, trades, taker_buy_volume FROM raw.crypto_kline_1m ... LIMIT 20` 中出现非 NULL 值；若 WS 不提供，则跑 `crypto-backfill` 后出现

