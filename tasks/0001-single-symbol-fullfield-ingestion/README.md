# 0001 - single-symbol-fullfield-ingestion

在不扰动现有生产链路的前提下，新建一个“隔离的 TimescaleDB 数据库”，并用现有 `markets-service` 的 `crypto-ws` 采集链路先只跑 1 个币种，实现 K 线全字段采集与可验证落库，为后续扩展到多币种/多市场打底。

## In Scope

- 新建一个独立 PostgreSQL/TimescaleDB 数据库（建议命名 `market_data_fullfield`），不复用现有 `market_data`。
- 对新库执行 `services-preview/markets-service/scripts/ddl/*.sql`，创建 `raw/quality/reference/agg/indicators` 等 schema 与 `raw.crypto_kline_1m` 等表。
- 启动 `services-preview/markets-service` 的加密采集模块，**raw 写入模式**，并通过环境变量将采集范围限制为单一币种（例如 `BTCUSDT`）。
- 给出可重复的验证命令：确认新库内有数据、字段齐全、只包含目标币种。

## Out of Scope

- 不修改 `config/.env`（该文件被视为生产配置，只读）。
- 不将新采集服务接入顶层 `./scripts/start.sh` 的“核心服务”编排（避免影响现有部署）。
- 不改动既有 TimescaleDB `market_data` 库的 schema/表/函数。
- 不做指标计算、Telegram 推送、信号检测与回测逻辑。

## 阅读与执行顺序（必须严格遵守）

1. `CONTEXT.md`：确认现状、约束、风险、假设是否成立
2. `PLAN.md`：确认方案选择与回滚协议
3. `ACCEPTANCE.md`：锁定验收口径（不要边做边改标准）
4. `TODO.md`：逐项执行（每步必须跑 Verify）
5. `STATUS.md`：实时记录证据与状态迁移

