# plugins/（消费与派生插件层）

本目录放所有“消费/派生”能力：**读 core/query → 计算/筛选/投递/展示**。  
原则：插件可以替换/扩展；事实层（`core/`）不被插件牵着走。

## 目录结构（扁平化）

说明：目录不再按 `derive/export/gateway/other` 做物理分层；统一扁平化到 `plugins/<service>/`。  
“派生/导出/网关/其它”属于**职责分类**，不属于目录层级。

```text
plugins/
├── trading/                  # 派生 writer：指标计算（写入 derived_data.indicator_snapshots.*）
├── signal/                   # 派生 writer：信号计算（写入 derived_data.signal_runtime.*）
├── ai/                       # AI：研究型/离线分析（只读直连 DB 的例外；不得对外提供新读出口）
├── llm-gateway/              # 网关：OpenAI-compatible（只读 Query Service）
├── telegram/                 # 投递：Telegram Bot（只读 Query Service）
├── sheets/                   # 投递：Google Sheets 同步（只读 Query Service）
├── predict/                  # 其它：预测市场（本地 bot / 日志型；逐步收口到 Query）
├── vis/                      # 其它：可视化/展示（只读 Query Service）
├── fate/                     # 其它：杂项/实验（以目录内说明为准）
└── nofx-dev/                 # 其它：外部仓库镜像（只读，用于对齐 nofx 契约）
```

## 依赖边界（硬规则）

- 分两类（必须讲清，否则会制造长期认知混乱）：
  - **派生 writer 插件**：`plugins/trading`、`plugins/signal`
    - 允许且必须直连 PostgreSQL，但**只允许写 `derived_data`**（不得写 `facts_data`）
    - 读侧仍应尽量通过 `core/query`（减少多头读；必要时可直连 derived 做回填/对账，但必须记录）
  - **消费/投递/网关插件**：`plugins/telegram`、`plugins/sheets`、`plugins/vis`、`plugins/llm-gateway`
    - **禁止直连 PostgreSQL/SQLite/日志等旁路数据源**
    - 只允许通过 `core/query` 的 `/api/v1/*` 读取（Single Reader）
- 明确例外：`plugins/ai` 允许**只读**直连 `facts_data` / `derived_data`（见 `assets/docs/architecture/architecture-exceptions.md`），但不得演化为常驻对外读出口。
