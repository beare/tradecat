# assets/database/db

这里存放的是 **DDL / 初始化脚本 / 迁移脚本**。
它们是“建库工具箱”，**不是当前运行库结构的单点真相源**。

## 当前运行真相

当前仓库的数据库分层已经收敛为：

```text
PostgreSQL:5433
├── facts_data
│   ├── market
│   └── alternative
├── derived_data
│   ├── indicator_snapshots
│   ├── signal_runtime
│   └── public
└── archive_data_20260328
```

- `facts_data`：事实层，只放 `core/*` 生产的数据
- `derived_data`：派生层，只放 `plugins/*` 生产的数据（当前主用 `indicator_snapshots`、`signal_runtime`）
- `archive_data_20260328`：归档/遗留对象

当前数据库真相文档见：

- `assets/docs/architecture/current-database-truth.md`
- `assets/docs/architecture/database-service-map.md`
- `assets/config/.env.example`（数据库对象命名契约单一真相源）
- `assets/common/contracts/db_contracts.py`（运行时共享读取入口）

## 服务产物落点（重点）

如果你要回答“**某个服务生产的数据到底写到哪里**”，直接看：

- `assets/docs/architecture/database-service-map.md`

最短口径如下：

- `core/market/binance`（group=lf）
  - `facts_data.market.binance_futures_um_candles_1m`
  - `facts_data.market.binance_futures_um_metrics_snapshot_5m`
- `core/market/binance`（group=hf）
  - `facts_data.market.binance_futures_um_*`
  - `facts_data.market.binance_spot_trades`
  - `facts_data.market.binance_option_*`
  - `facts_data.market.shared_*`
  - `facts_data.market.binance_ingest_*`
- `core/alternative/*`
  - `facts_data.alternative.*`
- `plugins/trading`
  - `derived_data.indicator_snapshots.*`
- `plugins/signal`
  - `derived_data.signal_runtime.*`
- `core/query` / `plugins/telegram` / `plugins/sheets` / `plugins/vis` / `plugins/llm-gateway`
  - 不写库，属于消费层
- `core/alternative/x`
  - 分组开关不落库，写项目内文件：`core/alternative/x/state/group_state.json`

## 本目录里这些 DDL 是什么

### 1) current / legacy / oneoff 三分层

- current：当前 bootstrap 真相入口
  - `assets/database/db/stacks/facts.sql`
  - `assets/database/db/stacks/derived.sql`
- legacy：历史兼容/对照入口
  - `assets/database/db/stacks/lf.sql`
  - `assets/database/db/stacks/hf.sql`
  - `assets/database/db/legacy_projects_tradecat/**`
- oneoff：一次性迁移 / 局部修复脚本
  - `assets/database/db/schema/003_add_all_intervals.sql`
  - `assets/database/db/schema/006_candles_meta_views.sql`
  - `assets/database/db/schema/014_crypto_futures_cm_trades_ids_swap.sql`
  - `assets/database/db/schema/015_crypto_spot_trades_fact_table.sql`
  - `assets/database/db/schema/020_crypto_futures_book_ids_swap.sql`
  - `assets/database/db/schema/optional/**`

细分清单见：

- `assets/docs/architecture/database-assets-classification.md`
- `assets/database/db/schema/README.md`
- `assets/database/db/stacks/README.md`

注意：

- 当前 bootstrap 真相已经切到：
  - `assets/database/db/stacks/facts.sql`
  - `assets/database/db/stacks/derived.sql`
- `lf.sql` / `hf.sql` 与 `scripts/ddl/facts_data_*.sql` 现在只承担历史迁移/收敛用途
- 旧兼容 schema 已退场；不要再把历史脚本里的旧命名当成当前运行真相
- 当前运行真相以 `facts_data.market.*`、`facts_data.alternative.*`、`derived_data.*` 为准

## 使用原则

### 初始化 facts_data

```bash
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d facts_data \
  -f assets/database/db/stacks/facts.sql
```

### 初始化 derived_data

```bash
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d derived_data \
  -f assets/database/db/stacks/derived.sql
```

## 重要约束

- `CREATE TABLE IF NOT EXISTS` 不会升级旧表结构
- 旧 schema 到新 schema 的迁移，必须依赖专门迁移脚本或受控 rename-swap
- 不要因为看到 DDL 里还保留旧命名，就假设运行库也必须保留旧 schema

## 配置映射

- `FACTS_DATABASE_URL`：事实库（默认 `facts_data`）
- `DERIVED_DATABASE_URL`：派生库（默认 `derived_data`）
- `DATABASE_URL`：兼容旧服务，默认仍指向 `facts_data`
- `BINANCE_VISION_DATABASE_URL`：可选；如果你给 hf（兼容旧名：binance-vision）单独拆实例/端口，再单独配置
- 数据库对象名（数据库/schema/固定表/文件态路径）：统一定义在 `assets/config/.env.example`
- 运行时不要直接手写对象名；统一通过 `assets/common/contracts/db_contracts.py` 读取

## 旧版快照

- `assets/database/db/legacy_projects_tradecat/`：旧项目路径拷贝，仅用于历史对照，不是当前真相源
