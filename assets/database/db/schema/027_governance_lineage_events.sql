-- ==================== governance: data lineage events (append-only) ====================
-- 目的：
-- - 记录“谁在什么时候，对哪个 resource_id 做了什么动作（produce/consume/transform）”
-- - 为后续：血缘审计 / 权限（ABAC/RBAC）/ 工作流 / 回滚定位 提供可查询事实
--
-- 分层约束（重要）：
-- - 为保持 core-only / plugins-only 写入边界，本表需要在两套库里各有一份：
--   - facts_data.governance.lineage_events（core 写入）
--   - derived_data.governance.lineage_events（plugins 写入）
-- - Query 侧通过 UNION 统一输出（不在 DB 层强耦合跨库）
--
-- 安全性：
-- - 本文件只做 CREATE IF NOT EXISTS，不做 DROP/TRUNCATE

CREATE SCHEMA IF NOT EXISTS governance;

CREATE TABLE IF NOT EXISTS governance.lineage_events (
    id BIGSERIAL PRIMARY KEY,

    -- 幂等键：sha256(actor_id|run_id|action|resource_id|occurred_at)（hex）
    event_key TEXT NOT NULL UNIQUE,

    -- 发生时间（业务时间）；created_at 记录“写入时间”
    occurred_at TIMESTAMPTZ NOT NULL,

    -- 角色/主体（服务、作业、脚本），例如：core/alternative/investing
    actor_id TEXT NOT NULL,

    -- 本次任务/批次 ID（建议服务端生成）；没有批次概念时可用进程启动 uuid
    run_id TEXT NOT NULL,

    -- 动作：MVP 先用文本；后续如需强约束可加 CHECK 或 ENUM
    action TEXT NOT NULL,

    -- 语义层锚点：必须来自 assets/catalog/resources.v1.yaml 的 resource_id（或将来被收敛进去）
    resource_id TEXT NOT NULL,

    -- success | failed
    status TEXT NOT NULL,

    -- 指标/摘要（禁止包含 DSN/Token/私钥等敏感信息）
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_governance_lineage_events_resource_time
ON governance.lineage_events (resource_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_governance_lineage_events_actor_time
ON governance.lineage_events (actor_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_governance_lineage_events_run_id
ON governance.lineage_events (run_id);
