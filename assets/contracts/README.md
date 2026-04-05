# 中央契约目录

本目录承接“语义层与物理契约集中化”的目标状态。

## 当前定位

- `assets/contracts/*`：运行时唯一真相源
- `assets/catalog/*`：导出投影层（通过 `python3 scripts/sync_catalog_projections.py` 同步，不参与运行时决策）
- `assets/common/contracts/db_contracts.py`：运行时 helper；central-covered 对象只从本目录派生
- 当前已纳入：
  - `core/market/binance` 主链对象与治理表
  - `core/alternative/news`
  - `core/alternative/telegram`
  - `core/alternative/discord`
  - `core/alternative/x/tg-unified`
  - `core/alternative/investing`
  - `core/alternative/hyperliquid`
  - `derived/indicator_snapshots/*`
  - `derived/signal_runtime/*`
  - `api/query/*`
  - `facts/governance/lineage_events`
  - `derived/governance/lineage_events`

## 当前收口状态

- `assets/contracts/resources.v1.yaml` 已覆盖当前 legacy catalog 中全部 resource_id
- `assets/contracts/probes.v1.yaml` 已覆盖当前 legacy catalog 中全部 source_id
- `assets/contracts/bindings.v1.yaml` 已补齐当前 central resources 的运行绑定或消费绑定
- 当前状态是：
  - `assets/contracts/*` = 中央真相源
  - `assets/catalog/*` = 导出 projection

## 文件职责

### `resources.v1.yaml`

定义：

- `resource_id`
- 语义属性
- canonical physical mapping

### `probes.v1.yaml`

定义：

- 每个资源如何被探测
- SQL / HTTP probe
- TTL 与运维说明

### `bindings.v1.yaml`

定义：

- 服务 / dataset / runtime group 与 `resource_id` 的绑定关系

## 迁移原则

1. 先引入中央契约
2. 再让 runtime / Query 从中央契约派生
3. 再收缩 `.env` 的对象命名职责
4. 最后删除 legacy fallback / 服务本地 physical mapping / central-covered 对象命名 env

## rollout / rollback

- rollout：
  1. 先把 resource 写进 `assets/contracts/resources.v1.yaml`
  2. 再把 service -> resource_id 写进 `assets/contracts/bindings.v1.yaml`
  3. 然后让 runtime helper / service binding 改成从中央 contracts 派生
4. 最后用守护脚本阻止旧的本地 canonical physical mapping 回潮
5. 同步兼容期 catalog projection：`python3 scripts/sync_catalog_projections.py`
- rollback：
  1. 先撤销 service binding 改动
  2. 再撤销 runtime helper 对中央 contracts 的依赖
  3. 最后删除本次新增的 contracts 资源与 binding
- 退出标准：
  - 服务源码不再裸写 canonical physical mapping
  - `assets/catalog/*` 已变成纯 projection，不再参与运行时解析
  - `./scripts/verify.sh` 中的 centralization 门禁为绿
  - central-covered 对象命名 env 已从模板与运行时回退中删除
