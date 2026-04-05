#!/usr/bin/env python3
"""
已退役：
- 当前仓库不再维护历史 SQLite 指标库到 RDS 的直同步脚本
- 当前真相入口：
  - facts_data -> assets/database/db/stacks/facts.sql
  - derived_data -> assets/database/db/stacks/derived.sql

如需复盘旧同步逻辑，请查 git history。
"""

raise SystemExit("scripts/sync_market_data_to_rds.py 已退役；请使用当前 facts/derived 数据链路。")
