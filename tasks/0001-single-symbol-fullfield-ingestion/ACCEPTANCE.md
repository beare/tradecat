# ACCEPTANCE - 精密验收标准

## 原子断言（Atomic Assertions）

### A1. 新库隔离成立（不污染既有库）

- 操作：创建新库（示例：`market_data_fullfield`），并将 `markets-service` 指向该库。
- Verify:
  - `psql -h localhost -p 5434 -U postgres -d postgres -c "\\l" | rg -n \"market_data_fullfield\"`
  - `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"SELECT current_database();\"`
- Gate:
  - `current_database()` 输出为 `market_data_fullfield`
  - 既有 `market_data` 库中**不出现**新建的 raw/quality 对象（抽查：`\\dn`）

### A2. DDL 完整落地（raw/quality 核心对象存在）

- Verify:
  - `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"\\dn\" | rg -n \"raw|quality|reference\"`
  - `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"\\dt raw.*\" | rg -n \"crypto_kline_1m\"`
  - `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"\\df quality.start_batch\"`
- Gate:
  - `raw.crypto_kline_1m` 存在
  - `quality.start_batch` 存在（用于批次血缘）

### A3. 单币种采集成立（只写入目标币种）

- 前提：以 `BTCUSDT` 为例。
- Verify:
  - 启动采集 2-3 分钟后，执行：
    - `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"SELECT COUNT(*) AS n FROM raw.crypto_kline_1m WHERE symbol='BTCUSDT';\"`
    - `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"SELECT COUNT(DISTINCT symbol) AS uniq FROM raw.crypto_kline_1m;\"`
- Gate:
  - `n > 0`
  - `uniq = 1`

### A4. 全字段采集可被证据证明（至少覆盖 Binance Kline 常见扩展字段）

- Verify:
  - `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"SELECT open_time, close_time, quote_volume, trades, taker_buy_volume, taker_buy_quote_volume FROM raw.crypto_kline_1m WHERE symbol='BTCUSDT' ORDER BY open_time DESC LIMIT 5;\"`
- Gate:
  - `open_time`、`close_time` 非空
  - `trades` 非空（允许为 0）
  - `quote_volume / taker_buy_*`：允许短期为 NULL，但在执行一次 `crypto-backfill --klines` 后，抽样至少出现 1 条非 NULL（作为“可补齐”的全字段保证）

## 边缘路径（Edge Cases，至少 3 个）

1. 数据库未就绪  
   - 预期：采集进程启动失败并在日志中给出明确错误（而不是静默卡死）。
2. 代理不可用（`127.0.0.1:9910` 连接失败）  
   - 预期：明确报错；若按 PLAN 进行修复（尊重 env/可关闭代理），可在无代理环境正常启动。
3. 重启幂等性  
   - 预期：重复启动/停止不导致数据爆炸式重复；主键冲突通过 upsert 覆盖，`COUNT(DISTINCT open_time)` 增长线性。

## 禁止性准则（Anti-Goals）

- 禁止修改 `config/.env`。
- 禁止对既有 `market_data` 库执行任何 DDL（包括 `CREATE TABLE/ALTER TABLE`）。
- 禁止把该实验性采集链路并入顶层“核心服务启动脚本”。

