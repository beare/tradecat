-- 000_timescaledb_extension.sql
--
-- 目标：
-- - 仅确保 TimescaleDB 扩展存在（不创建任何表/视图）。
--
-- 用途：
-- - 供事实库 bootstrap 先行安装扩展，再按需加载 001/004/005/007 等当前 DDL。
-- - 当前真相已不再使用旧兼容 K 线口径。

CREATE EXTENSION IF NOT EXISTS timescaledb;
