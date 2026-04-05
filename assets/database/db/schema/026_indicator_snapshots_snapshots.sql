-- 026_indicator_snapshots_snapshots.sql
--
-- 目的：
-- - 为 indicator_snapshots 治理提供“单表 + JSONB payload”的收敛存储形态
-- - 作为 Query Service / plugins 的稳定读契约（避免继续在源码里到处硬编码 38 张表名）
--
-- 说明：
-- - legacy 多表仍保留（用于回滚/对照）；写端可通过 INDICATOR_PG_LAYOUT=dual|snapshot 开启双写
-- - 该表存储的 payload 应包含历史列（含 "交易对"/"周期"/"数据时间"），以保证消费侧兼容

CREATE TABLE IF NOT EXISTS indicator_snapshots.snapshots (
  indicator text NOT NULL,
  symbol text NOT NULL,
  period text NOT NULL,
  data_time text NOT NULL,
  payload jsonb NOT NULL,
  collected_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (indicator, symbol, period, data_time)
);

-- 常用查询：
-- - 单币/单周期最新：WHERE indicator=? AND symbol=? AND period=? ORDER BY data_time DESC LIMIT 1
-- - 每币最新（排行）：DISTINCT ON(symbol) ... ORDER BY symbol, data_time DESC
CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_snapshots_indicator_period_symbol_time
  ON indicator_snapshots.snapshots (indicator, period, symbol, data_time);

CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_snapshots_indicator_time
  ON indicator_snapshots.snapshots (indicator, data_time);

