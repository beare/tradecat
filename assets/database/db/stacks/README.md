# stacks 目录说明

## current

- `facts.sql`：当前 `facts_data` bootstrap 入口
- `derived.sql`：当前 `derived_data` bootstrap 入口

## legacy

- `lf.sql`：历史兼容入口，不再作为当前默认真相
- `hf.sql`：历史兼容入口，不再作为当前默认真相

## 使用原则

- 新环境默认只执行：
  - `assets/database/db/stacks/facts.sql`
  - `assets/database/db/stacks/derived.sql`
- `lf.sql` / `hf.sql` 只用于旧环境对照、迁移复盘或历史回放
