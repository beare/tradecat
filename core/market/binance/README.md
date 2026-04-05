# core/market/binance

这是 Binance 域在 TradeCat 中的唯一工程根。当前结构已经收口为：**一个工程壳、一个源码根、两类运行分组（LF/HF）、多个 dataset 模块**。

## 当前结构

```text
core/market/binance/
├── pyproject.toml
├── Makefile
├── scripts/
│   ├── start.sh
│   ├── check_structure_contract.sh
│   └── capture_ccxt_pro_um_book_data.py
├── src/binance/
│   ├── config.py
│   ├── registry.py
│   ├── service_entry.py
│   ├── common/
│   ├── sources/
│   │   ├── binance_api/
│   │   └── archive_source/
│   ├── storage/
│   ├── runtime/
│   └── datasets/
│       ├── futures_um_candles_1m/
│       ├── futures_um_metrics_snapshot_5m/
│       ├── futures_um_trades/
│       ├── futures_um_book_ticker/
│       ├── futures_um_book_depth/
│       ├── spot_trades/
│       ├── spot_candles_1m/
│       └── _reserved/
├── tests/
└── var/
    ├── lf/
    └── hf/
```

## 运行分组

- `LF`
  - 数据集：`futures_um_candles_1m`、`futures_um_metrics_snapshot_5m`
  - 运行态目录：`core/market/binance/var/lf`
- `HF`
  - 数据集：`futures_um_trades`、`futures_um_book_ticker`、`futures_um_book_depth`、`spot_trades`
  - 运行态目录：`core/market/binance/var/hf`
- `Passive`
  - 对象：`spot_candles_1m`
  - 物理落点：`market.binance_cagg_spot_klines_1m`
  - 维护方式：由 `spot_trades` 的连续聚合自动维护，不属于 LF/HF runtime group

> `LF/HF` 是运行分组，不是子工程目录。

## 唯一入口

```bash
cd core/market/binance
./scripts/start.sh start
./scripts/start.sh status
./scripts/start.sh stop
```

### 启用 HF canary 示例

```bash
cd core/market/binance
BINANCE_STACK_ENABLE_HF=1 BINANCE_DATASET_FUTURES_UM_TRADES_ENABLED=1 BINANCE_HF_SERVICE_MODE=collect BINANCE_HF_COLLECT_DATASET=crypto.data.futures.um.trades BINANCE_HF_COLLECT_SYMBOLS=BTCUSDT ./scripts/start.sh start
```

### 查看 dataset 真相矩阵与诊断

```bash
cd core/market/binance
PYTHONPATH=./src python3 -m binance.service_entry plan
PYTHONPATH=./src python3 -m binance.service_entry doctor
PYTHONPATH=./src python3 -m binance.service_entry audit
PYTHONPATH=./src python3 -m binance.service_entry canary
```

## 工程入口

```bash
cd core/market/binance
make plan
make doctor
make audit
make canary
make db-audit
make status
make test
```

## 生产判活口径

- `doctor`
  - 看当前配置是否合法
  - 看 LF active dataset 是否仍在持续推进
  - 输出字段：`freshness_s`、`lag_s`、`rows_written`
- `audit`
  - 看最近一轮 `ws` / `metrics` / `backfill` 是否可信
  - 输出 `ok|warn|stale|missing|anomaly`
  - 用于 `restart` 后首轮复核和定时巡检
- `db-audit`
  - 看 Timescale hypertable / compression / retention / CAGG / integer_now_func 是否仍符合生产真相
  - 失败时先运行治理修复脚本，再重新审计
- `canary`
  - 看 HF 当前是否满足“单 dataset、单 symbol、短时 smoke”的启用前检查
  - 输出验证与回退命令

当前 LF 判活阈值：
- `futures_um_candles_1m`：`freshness_s <= 180` 视为健康
- `futures_um_metrics_snapshot_5m`：`freshness_s <= 420` 视为健康
- `lf_backfill`：`freshness_s <= 600` 且 `repeated_fill_detected=0` 视为健康

## Timescale 数据库治理

审计命令：

```bash
cd core/market/binance
./scripts/check_timescale_governance.sh
```

修复命令：

```bash
cd core/market/binance
./scripts/repair_timescale_governance.sh
```

当前治理口径：

- `market.binance_futures_um_candles_1m`
  - hypertable，chunk=`1 day`
  - compression=`3 days`
  - retention=`180 days`
- `market.binance_futures_um_metrics_snapshot_5m`
  - hypertable，chunk=`7 days`
  - compression=`7 days`
  - retention=`none`
- `market.binance_futures_um_trades`
  - hypertable，integer now=`market.unix_now_ms`
  - compression=`30 days`
  - retention=`none`
- `market.binance_futures_um_book_ticker`
  - hypertable，integer now=`market.unix_now_ms`
  - compression=`3 days`
  - retention=`none`
- `market.binance_futures_um_book_depth`
  - hypertable，integer now=`market.unix_now_ms`
  - compression=`30 days`
  - retention=`none`
- `market.binance_spot_trades`
  - hypertable，integer now=`market.unix_now_us`
  - compression=`30 days`
  - retention=`none`
- `market.binance_futures_um_metrics_atomic`
  - residual raw hypertable
  - compression=`90 days`
  - retention=`none`
- `market.binance_cagg_spot_klines_1m`
  - passive continuous aggregate
  - refresh policy=`every 1 minute`

## LF gap/backfill 治理

- LF 的 gap 扫描窗口统一只看“已结束的 UTC 自然日”
- smart gapfill 与定时 backfill 共用同一套 UTC 窗口与治理规则
- 单个 `symbol/day` 若补后仍未闭合，会进入 `ignored` 冷却态，避免 5 分钟风暴重试
- 冷却期后再次探测到同一 gap，会自动 `reopen` 后重试
- 治理证据统一落在 `market.binance_ingest_gaps`

## HF canary 运行手册

- HF 默认仍然关闭；只有 canary 场景允许短时开启
- canary 硬约束：
  - 只允许启用 1 个 HF dataset
  - 只允许 1 个 symbol
  - 必须显式设置 `BINANCE_HF_SERVICE_MODE`
  - 必须保留证据目录：默认 `core/market/binance/var/hf/evidence`

标准流程：

```bash
cd core/market/binance
PYTHONPATH=./src python3 -m binance.service_entry canary
BINANCE_STACK_ENABLE_HF=1 BINANCE_DATASET_FUTURES_UM_TRADES_ENABLED=1 BINANCE_HF_SERVICE_MODE=collect BINANCE_HF_COLLECT_DATASET=crypto.data.futures.um.trades BINANCE_HF_COLLECT_SYMBOLS=BTCUSDT ./scripts/start.sh start
PYTHONPATH=./src python3 -m binance.service_entry canary verify
PYTHONPATH=./src python3 -m binance.service_entry canary rollback
```

canary 相关环境变量：

- `BINANCE_HF_CANARY_SMOKE_SECONDS`
  - 默认 `120`
  - 用于 verify 判断 smoke 窗口
- `BINANCE_HF_CANARY_EVIDENCE_DIR`
  - 默认 `var/hf/evidence`
  - rollback 时会把 `service.log` / `service.meta` / `service.pid` 归档到该目录

## 配置

- `BINANCE_STACK_ENABLE_LF=1`
- `BINANCE_STACK_ENABLE_HF=0`
- `BINANCE_STACK_DRY_RUN=1`
- `DATA_SERVICE_DATABASE_URL`：LF 写库 DSN
- `BINANCE_HF_DATABASE_URL`：HF 写库 DSN
- HF 运行模式：`BINANCE_HF_SERVICE_MODE=collect|backfill|repair`
- dataset 细开关（默认 LF 开、HF 全关；`spot_candles_1m` 默认关且不参与 runtime 编排）：
  - `BINANCE_DATASET_FUTURES_UM_CANDLES_1M_ENABLED`
  - `BINANCE_DATASET_FUTURES_UM_METRICS_SNAPSHOT_5M_ENABLED`
  - `BINANCE_DATASET_FUTURES_UM_TRADES_ENABLED`
  - `BINANCE_DATASET_FUTURES_UM_BOOK_TICKER_ENABLED`
  - `BINANCE_DATASET_FUTURES_UM_BOOK_DEPTH_ENABLED`
  - `BINANCE_DATASET_SPOT_TRADES_ENABLED`
  - `BINANCE_DATASET_SPOT_CANDLES_1M_ENABLED`

## 一句话原则

- 一个工程根
- 一个源码根
- dataset-first
- `LF/HF` 只是运行分组
- `var/lf` / `var/hf` 是运行态，不是工程边界
