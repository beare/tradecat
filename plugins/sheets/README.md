# sheets-service

将本地生成的“TG 卡片事件（CardEvent）”同步到 Google Sheets 公共看板。

## 配置（环境变量）

说明：
- `scripts/start.sh` 会按顺序加载：`<repo>/assets/config/.env`（只读）→ `<service>/.env`（本地私密，不提交）。
- 你也可以直接在 shell 里 `export` 这些变量（纯 CLI）。

- `SHEETS_WRITE_MODE`：写入模式 `webhook|sa`（默认 `webhook`）
- `SHEETS_SYNC_MODE`：同步模式 `dashboard|snapshot|append`
  - `dashboard`（SA 推荐，默认）：每轮 **reset 看板并全量重绘**，紧凑排布，不依赖 slot 预留高度，不会出现“卡片间空洞/错位/堆叠”。
  - `snapshot`：走 outbox + 幂等，只写“新卡片事件”；适合需要事实表 append 的场景（幂等状态写入 Sheets 隐藏 tab 或本地文件）。
  - `append`：保留口径（目前与 `snapshot` 行为一致，历史兼容）。
- `SHEETS_FORCE_RENDER`：`1` 表示强制重渲染（忽略幂等，用于版式/样式大改后刷新）

### Webhook 模式（Apps Script，可选）
- `SHEETS_WEBHOOK_URL`：Apps Script Web App URL（`.../exec`）
- `SHEETS_WEBHOOK_SECRET`：HMAC 密钥
- `SHEETS_WEBHOOK_TIMEOUT_SECONDS`：请求超时（默认 10）
- `SHEETS_WEBHOOK_MAX_RETRIES`：失败重试次数（默认 3；仅对 429/5xx/网络错误生效）
- `SHEETS_WEBHOOK_BACKOFF_BASE_SECONDS`：退避基数（默认 1.0）
- `SHEETS_WEBHOOK_BACKOFF_MAX_SECONDS`：退避上限（默认 30.0）

### SA 模式（Service Account + Sheets API，全 CLI，推荐）
- `GOOGLE_APPLICATION_CREDENTIALS`：SA key.json 路径（或 `SHEETS_SA_CREDENTIALS_PATH`）
- `SHEETS_SPREADSHEET_ID`：目标工作簿 id（可用 `--bootstrap` 创建）
- `SHEETS_PUBLIC_READ`：`1` 表示将工作簿设为“任何人有链接可读”
- `SHEETS_SHARE_EMAIL`：可选，授权某个邮箱为 writer（不发通知）
- `SHEETS_DRIVE_FOLDER_ID`：可选，把工作簿/Blob 放入指定 Drive 目录
- `SHEETS_DASHBOARD_COL_L` / `SHEETS_DASHBOARD_COL_R`：看板列区间（默认：多周期 `A..BS`，否则 `A..M`）
- `SHEETS_DASHBOARD_AUTO_WIDTH`：`0/1`（默认 `1`；`dashboard` 模式下自动把 `col_r` 扩到“足以容纳本轮最大列数”，避免“超宽表头被纵向分块”让人误以为列丢失）
- `SHEETS_DASHBOARD_MODE`：`replace|append`（默认 `replace`；同类卡片覆盖写，避免持续堆叠）
- `SHEETS_DASHBOARD_SLOT_HEIGHT`：replace 模式槽位“最小预留高度”（行数，默认 10；实际预留高度会随卡片高度增长并记录在 `元数据.slot.<card_type>.h`）
- `SHEETS_FACTS_MODE`：`append|none`（默认 `append`；若工作簿触发 1000 万 cells 上限，需要设为 `none` 仅保留看板覆盖写）
- `SHEETS_BLOB_THRESHOLD_CHARS`：raw 超长阈值（默认 20000；超长会落 Drive 并在表内存引用）
- `SHEETS_SA_WRITE_RPM`：SA 写入限流（写请求/分钟，默认 55；配额为 60 时建议 50）
- `SHEETS_SA_READ_RETRIES`：读请求弱网重试次数（默认 3；覆盖 `SSLError/ConnectionResetError/socket.timeout/5xx/429` 等瞬断）
- `SHEETS_SA_429_RETRIES`：写请求遇到 429 的重试次数（默认 8；指数退避）
- `SHEETS_SA_NET_WRITE_RETRIES`：写请求弱网/代理抖动重试次数（默认 2；仅对“幂等写入”生效，避免 append 重放）
- `SHEETS_PRUNE_TABS_INTERVAL_SECONDS`：minimal schema 下 prune_tabs 的最小执行间隔（默认 21600=6h；keep 集合变更会绕过间隔立即执行；状态写入 `local_meta.json`）
- `SHEETS_LOG_LEVEL`：日志级别（`info|debug`，默认 `info`；`debug` 才会输出 `[DEBUG]` 细节）

## 公开工作簿契约 v1（对外可消费 API）

范围：只治理/审计 **可见 tabs**（hidden tabs 不纳入契约与样式统一）。

### 顶部两行（全局硬契约）

每个可见 tab 的前两行必须为：
- Row1(A1)：广告位 banner（单单元格，多行文本；禁止 merge；B1:.. 清空）
- Row2(A2)：元信息 meta（单单元格，单行文本；禁止 merge；B2:.. 清空）

元信息行格式：用中文逗号 `，` 分隔，**key/value 交替**，空值统一用 `null`。

### 全局样式（公开表默认无边框）
- `hideGridlines=true`（隐藏默认网格线）
- `frozenRowCount >= 3`（至少冻结 banner + meta + header）
- 正文区域不应出现“深色边框线”（UI 看起来应为无边框）

### API tab（机器消费目录表）

`API` tab 的 A/B/C 三列约定：
- A：单元格 JSONL（gzip+base64）
- B：说明（该端点返回的数据）
- C：请求命令（外部消费者可直接复制执行）

### 审计与发布门禁（强烈建议）

审计命令（SA 模式，只读）：
- `.venv/bin/python -m src --audit-top-rows`
- `.venv/bin/python -m src --audit-grid`
- `.venv/bin/python -m src --audit-sensitive-public`
- `.venv/bin/python -m src --audit-public-styles`

发布门禁（写入后自动跑上述审计；失败返回非 0）：
- `SHEETS_PUBLIC_PUBLISH_GUARD=1`

### 自愈/回滚（表格被手工改乱时）

按“破坏面”最小的顺序回滚：
1) 顶部两行被破坏：`.venv/bin/python -m src --fix-top-rows`
2) 仍有线条/边框残留：`.venv/bin/python -m src --style-borderless-all`
3) API tab 过期：`.venv/bin/python -m src --publish-api-table`
4) 可见 tabs 顺序乱：`.venv/bin/python -m src --reorder-tabs-public`
5) 网格异常膨胀：`.venv/bin/python -m src --compact-grid-public`

### 指标数据源（Query Service）

sheets-service 复用 telegram-service 的卡片导出逻辑，指标/行情快照一律通过 Query Service（`/api/v1`）读取。

- `QUERY_SERVICE_BASE_URL`：Query Service 基地址（必填，例如 `http://127.0.0.1:8088`）
- `QUERY_SERVICE_TOKEN`：可选，内网 token（Header: `X-Internal-Token`）
- `QUERY_SERVICE_TIMEOUT_SECONDS` / `QUERY_SERVICE_CACHE_TTL_SECONDS`：可选，HTTP 超时/缓存 TTL

说明：Query Service 内部直连数据库完成查询，消费端只需要配置 Query Service 的地址与 token。

### 导出与运行
- `SHEETS_EXPORT_LANG`：默认 `zh_CN`
- `SHEETS_EXPORT_CARDS`：逗号分隔 card_id 白名单；空=全部
- `SHEETS_EXPORT_INCLUDE_BLACKLIST`：`1` 表示包含 cards registry 黑名单卡片
- `SHEETS_EXPORT_MULTI_PERIODS`：`0/1`（默认 `1`；对排行榜卡片导出 7 周期横向表：`1m..1w`）
  - 列顺序：按“字段组”展开周期（`趋势强度@1m..1w` → 下一字段组 …），并在看板渲染时生成“两行表头”：字段组行 + 周期行。
- `SHEETS_HIDE_PERIODS`：隐藏指定周期列（仅影响展示，不删除数据；默认 `1m`）
  - 示例：`SHEETS_HIDE_PERIODS=1m` / `SHEETS_HIDE_PERIODS=1m,1w`
  - 禁用：`SHEETS_HIDE_PERIODS=off`
- 固定列宽（可选，优先级最高）：用于“你在表格 UI 手工拖拽调整好列宽后，希望后续刷新不再覆盖”
  - `SHEETS_DASHBOARD_FIXED_COL_WIDTHS`：看板每列像素宽度列表（从 `SHEETS_DASHBOARD_COL_L` 起），逗号/中文逗号分隔；长度不足会用最后一个值补齐，超出会截断
  - `SHEETS_SYMBOL_QUERY_FIXED_COL_WIDTHS`：币种查询表每列像素宽度列表（从 A 列起），规则同上
  - CLI 快照（只读，不写入 `.env`）：
    - `.venv/bin/python -m src --snapshot-col-widths`（输出：看板/币种查询/Polymarket 三表共 5 行 env）
    - `.venv/bin/python -m src --snapshot-polymarket-col-widths`（仅输出 Polymarket 三表 env，历史兼容）
- 顶部广告/赞助商栏（可选）：在看板顶部新增首行（A1）用于放置赞助商/返佣信息
  - `SHEETS_DASHBOARD_BANNER_TEXT`：看板广告栏文本（优先）
  - `SHEETS_TOP_BANNER_TEXT`：全局广告栏文本（当 `SHEETS_DASHBOARD_BANNER_TEXT` 为空时回退使用）
- 公开工作簿统一顶部两行（对外可消费约束）：
  - A1：广告位（单单元格，多行文本）
  - A2：元信息行（单单元格，中文逗号 `key，value` 交替；空值统一 `null`）
  - 安全兜底：元信息行会对常见绝对路径做脱敏（`/home/...` → `<path:xxx>`），避免把内部路径写入公开表
- API tab（公开读取/机器消费，可选）：在工作簿新增 `API` 子表，每个非 API tab 对应一行单元格 JSON（gzip+base64），可被外部稳定消费
  - `SHEETS_API_ENABLE`：`0/1`（默认 `0`；仅 SA 模式支持）
  - `SHEETS_API_TAB_TITLE`：API 子表名称（默认 `API`）
  - `SHEETS_API_INTERVAL_SECONDS`：最小刷新间隔（默认 900）
  - `SHEETS_API_MAX_TABS`：最多处理 tab 数（默认 80；防止误配导致读取/写入爆炸）
  - `SHEETS_API_ONLY_TITLES`：仅导出指定 tab（逗号/中文逗号分隔；支持前缀通配 `看板_*`）
  - `SHEETS_API_SKIP_TITLES`：跳过指定 tab（逗号/中文逗号分隔；支持前缀通配 `看板_*`）
  - `SHEETS_API_READ_COL_R` / `SHEETS_API_READ_MAX_ROWS`：读取二维表范围上限（避免误配导致读取爆炸）
  - `SHEETS_API_MAX_CHARS`：单元格最大字符数软上限（默认 48000；超出将按 facts 截断或降级为 preview/error envelope）
  - `SHEETS_API_TRUNCATE_MAX_FACTS`：facts 超长时最多保留多少条（默认 1200；兼容 legacy：`SHEETS_API_TRUNCATE_MAX_ROWS`）
  - `SHEETS_API_TRUNCATE_PREVIEW_FACTS`：仍超长时的 preview 尺寸（默认 50；兼容 legacy：`SHEETS_API_TRUNCATE_PREVIEW_ROWS`）
  - `SHEETS_PRUNE_KEEP_API_TAB`：minimal schema 的 `--prune-tabs` 是否保留 API tab（默认 `1`）
  - 审计核查（只读）：`.venv/bin/python scripts/audit_api_table.py --public-check`
- `SHEETS_EXPORT_SYMBOLS_GROUPS`：导出侧覆盖 `SYMBOLS_GROUPS`（例如 `main4`），避免继承全局配置导致看板币种不全
- `SHEETS_EXPORT_SYMBOLS_UNFILTERED`：`0/1`（`1` 表示导出侧强制关闭币种过滤，等价 `SYMBOLS_GROUPS=auto`）
  - 常见现象：如果你的全局 `assets/config/.env` 是 `SYMBOLS_GROUPS=main1`（仅 BTC），看板会“只有 BTC”。此时无需改全局配置，只需在 sheets-service 侧设置以上变量即可。
- 看板源信息：每张卡片的 `标题/更新/排序/提示/最后更新` 会按固定顺序拼接到 **同一单元格**（整行合并），紧贴在表格主体上方。
- `SHEETS_SYMBOL_TABS`：逗号分隔交易对（默认取 `SYMBOLS_GROUP_main4`，再回退 `BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT`），为每个交易对创建一个中文前缀的子表 `币种查询_<SYMBOL>` 并覆盖写“币种查询真表格（结构化字段）”。
- `SHEETS_SYMBOL_TAB_PREFIX`：子表名前缀（默认 `币种查询_`）
- `SHEETS_SYMBOL_TABS_MODE`：`dashboard|every|none`（默认 `dashboard`；仅在 dashboard 全量重绘时刷新子表；`every` 表示 snapshot 模式也刷新，写入量更大）
- `SHEETS_SYMBOL_TABS_INTERVAL_SECONDS`：子表刷新最小间隔（默认 900；仅对 `dashboard` 模式下的子表刷新节流生效）
- `SHEETS_SYNC_INTERVAL_SECONDS`：daemon 模式间隔（默认 60）
- `SHEETS_UNIFIED_REFRESH`：`0/1`（默认 `0`；`1` 表示由 daemon tick 统一刷新：每轮把“币种查询子表 + Polymarket”也完整刷新一次，不再使用各自的 interval 节流）

### 本地 dry-run / 离线验收

- `--dry-run` 或 `SHEETS_SYNC_DRY_RUN=1`：允许在**没有 webhook / SA 真实写入配置**时跑完整导出链，只验证 Query 读取、卡片生成、outbox 组装与主流程控制，不发真实写入请求。
- daemon 模式下如果缺少真实写入配置且**未开启 dry-run**，进程会直接退出；不会再以“假运行”状态循环报错。

### 实时新闻（旁路子表，可选）

在同一工作簿内新增一个子表（默认名：`实时新闻`），展示最近窗口内的“实时新闻”列表，供看板阅读或 AI 消费。
当前表结构：`时间(北京) | 内容 | 重要性分 | 等级 | 标签 | 理由 | 评分时间(北京) | 模型`（schema=`news_v2`）。

- `SHEETS_NEWS_ENABLE`：`0/1`（默认 `0`；仅 SA 模式支持；开启后会在 dashboard 重绘流程里旁路刷新）
- `SHEETS_NEWS_INTERVAL_SECONDS`：最小刷新间隔（默认 300；节流避免配额爆炸）
- 窗口与截断：
  - `SHEETS_NEWS_WINDOW_HOURS`：滚动窗口小时数（默认 24）
  - `SHEETS_NEWS_LIMIT`：最多导出条数（默认 200；建议对外消费保持紧凑）
  - `SHEETS_NEWS_MAX_TEXT_CHARS`：单条内容最大字符数（默认 2000；超长截断）
  - `SHEETS_NEWS_TIMEOUT_SECONDS`：取数超时（默认 30）
- 数据源（单读出口）：
  - 必须通过 Query Service 事件域端点读取：`/api/v1/events?tag=新闻&types=news`
  - 事件域端点 **强制鉴权（fail-closed）**：必须配置 `QUERY_SERVICE_TOKEN` 并通过 Header `X-Internal-Token` 传入
- 写入前过滤（P0 合规闸门：公开表强制启用，防止版权/水印污染）：
  - `SHEETS_NEWS_FILTER_ENABLE`：`0/1`（默认 `1`；**当 `SHEETS_PUBLIC_READ=1` 时即使设为 `0` 也会被强制启用**；设为 `0` 仅用于弱化“非核心过滤”，但仍会保留少量强制合规闸门）
  - 强制合规词（无法通过表格配置移除）：`trim` 至少包含 `金十数据`；`blacklist` 至少包含 `金十图示`、`金十数据中心工具`
- `SHEETS_NEWS_FILTER_CONFIG_FROM_SHEET`：`0/1`（默认 `1`；若工作簿存在 `新闻过滤配置` tab，则优先读取其配置；否则回退 repo 内 `core/alternative/news/configs/filter.json`）
  - `SHEETS_NEWS_FILTER_CONFIG_TTL_SECONDS`：读取配置 TTL 秒（默认 `300`；用于 `--daemon-news` 高频刷新避免 read quota 爆炸）
- LLM 结构化评分（默认启用）：
  - `SHEETS_NEWS_LLM_ENABLE`：`0/1`（默认 `1`）
  - `SHEETS_NEWS_LLM_MODEL`：模型名（默认 `qwen3.5-plus`）
  - `SHEETS_NEWS_LLM_API_BASE_URL`：可选覆盖（留空回退 `LLM_API_BASE_URL`）
  - `SHEETS_NEWS_LLM_API_KEY`：可选覆盖（留空回退 `EXTERNAL_API_KEY`）
  - `SHEETS_NEWS_LLM_TIMEOUT_SECONDS`：调用超时（默认 `20`）
  - `SHEETS_NEWS_LLM_MAX_TOKENS`：最大输出 token（默认 `256`）
  - `SHEETS_NEWS_LLM_MAX_NEW_PER_RUN`：常规刷新每轮最多新算条数（默认 `8`）
  - `SHEETS_NEWS_LLM_MAX_NEW_PER_RUN_FAST`：`--daemon-news` 高频刷新每轮最多新算条数（默认 `2`）
  - `SHEETS_NEWS_LLM_REASON_MAX_CHARS`：`理由` 列最大字符数（默认 `80`）
  - `SHEETS_NEWS_LLM_PROMPT_VERSION`：提示词版本号（默认 `news_score_v1`）
- `SHEETS_NEWS_LLM_CACHE_PATH`：评分缓存 sqlite 路径（留空回退到服务 `data/`）
- 失败降级：该行评分字段写 `"null"`，不会阻断新闻导出主流程
- 高频模式（仅刷新新闻，不重绘看板）：使用 `--daemon-news`
  - `SHEETS_NEWS_DAEMON_INTERVAL_SECONDS`：刷新间隔秒（支持小数；例如 `1.5`）
  - `SHEETS_NEWS_DAEMON_LOG_INTERVAL_SECONDS`：日志输出间隔秒（默认 30；避免 1.5s 刷新刷爆日志）
  - `SHEETS_NEWS_VALUES_ONLY_LIMIT_CAP`：values-only 覆盖写硬上限（默认 300；防止误配导致配额雪崩）
  - `SHEETS_NEWS_STYLE_INTERVAL_SECONDS`：样式维护线程周期（默认 `0`，仅启动后做一次样式修复）
  - `SHEETS_NEWS_STYLE_RETRY_SECONDS`：样式修复失败重试间隔（默认 `15`）
  - `SHEETS_NEWS_STYLE_MAX_RETRIES`：仅一次性修复模式生效；`0` 表示无限重试
- 入口 tab：
  - `SHEETS_TAB_NEWS`：子表名称（默认 `实时新闻`）
  - `SHEETS_NEWS_HIDE_TAB`：`0/1`（默认 `0`）
- `SHEETS_PRUNE_KEEP_NEWS_TAB`：`0/1`（默认 `1`；`--prune-tabs` 时保留该 tab）
- `SHEETS_PRUNE_KEEP_NEWS_FILTER_TAB`：`0/1`（默认 `1`；`--prune-tabs` 时保留 `新闻过滤配置` tab，避免在线维护黑名单/删词被误删）

### 新闻过滤配置（可选）

目标：把上游 `core/alternative/news` 的“过滤/黑名单规则”托管到同一份 Google Sheets 里，做到**在线改规则、无需 ssh 上服务器改文件**。

1) 创建/刷新模板 tab（SA 模式）：
- 创建模板（若 tab 已存在则跳过）：`.venv/bin/python -m src --publish-news-filter-config`
- 强制覆盖写模板（危险：会覆盖表格里已编辑的配置）：`.venv/bin/python -m src --publish-news-filter-config --force`
- 或启用“引导式创建”（默认只创建一次，避免覆盖你在表格里维护的配置）：`SHEETS_NEWS_FILTER_CONFIG_ENABLE=1`（并运行一次 dashboard 刷新）

2) 表格结构（tab 默认名：`新闻过滤配置`）：
- A 列：`键(key)`：`minLength|maxLength|blacklist|trim`
- B 列：`值(value)`：数值或关键词（每行一个）
- C 列：`启用`：`1/0`（`0` 表示忽略该行）
- D 列：`说明`：可选

3) 让 `core/alternative/news` 从表格拉取配置（建议工作簿设为“任何人有链接可读”）：
- `NEWS_FILTER_CONFIG_URL`：远程配置 URL（支持 JSON 或 Google Sheets CSV）
  - CSV URL 示例：`https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/gviz/tq?tqx=out:csv&sheet=<TAB_TITLE>`
- `NEWS_FILTER_REFRESH_MS`：刷新间隔（默认 `60000`）
- `NEWS_FILTER_CONFIG_TIMEOUT_MS`：拉取超时（默认 `5000`）

说明：一旦 `NEWS_FILTER_CONFIG_URL` 拉取成功，会覆盖本地 `core/alternative/news/configs/filter.json` 的生效配置（拉取失败则继续使用本地/上一次成功配置）。

### Polymarket 统计（旁路子表，可选）

在同一工作簿内新增一个子表（默认名：`Polymarket统计`），展示服务器 polymarket 服务的 `csv-report.js` 统计输出。

- `SHEETS_TAB_POLYMARKET_STATS`：子表名称（默认 `Polymarket统计`）
- `SHEETS_POLYMARKET_STATS_ENABLE`：`0/1/auto`（默认 `auto`；`auto` 表示“能导出就导出”，否则跳过）
- `SHEETS_POLYMARKET_STATS_INTERVAL_SECONDS`：最小刷新间隔（默认 900）
- `SHEETS_POLYMARKET_MODE`：`auto|local|ssh`（默认 `auto`；优先 ssh）
- `SHEETS_POLYMARKET_SERVICE_DIR`：polymarket 服务目录（包含 `scripts/csv-report.js`）
- `SHEETS_POLYMARKET_LOG_FILE`：日志路径（相对 `SERVICE_DIR` 或绝对路径；默认优先 `$HOME/.local/state/tradecat/polymarket.log`，否则回退 `logs/polymarket_bot.log`）
- `SHEETS_POLYMARKET_MAX_LOG_MB`：日志最大大小（默认 200；防止误扫 7GB 导致超时）
- `SHEETS_POLYMARKET_TIMEOUT_SECONDS`：导出超时（默认 30）
- `SHEETS_POLYMARKET_TRANSLATE`：`0/1`（默认 `0`；禁用翻译避免外部依赖与副作用）
- `SHEETS_POLYMARKET_ENABLE_API_RANKINGS`：`0/1`（默认 `0`；`1` 才会请求 polymarket gamma API）
- `SHEETS_POLYMARKET_SSH_HOST/SHEETS_POLYMARKET_SSH_USER/SHEETS_POLYMARKET_SSH_KEY_PATH`：ssh 参数（可选）
- `SHEETS_POLYMARKET_REMOTE_SERVICE_DIR` / `SHEETS_POLYMARKET_REMOTE_LOG_FILE`：ssh 模式下覆盖远端路径（可选；默认优先 `$HOME/.local/state/tradecat/polymarket.log`）
- `SHEETS_POLYMARKET_COMPACT_GRID`：`0/1`（默认 `1`；收缩网格，让右侧无单元格）
- 冻结（阅读体验）：
  - `SHEETS_POLYMARKET_FROZEN_COLS`：冻结左侧列数（默认 `2`，冻结到 B 列：面板列 + 关键列）
  - `SHEETS_POLYMARKET_FROZEN_ROWS`：冻结顶部行数（留空=自动冻结到首个分段表头；也可显式指定整数）
- `SHEETS_PRUNE_KEEP_POLYMARKET_STATS`：`0/1`（默认 `1`；`--prune-tabs` 时保留该 tab）
- 固定列宽（可选，优先级最高）：
  - `SHEETS_POLYMARKET_FIXED_COL_WIDTHS_TOP15`
  - `SHEETS_POLYMARKET_FIXED_COL_WIDTHS_TIMESLOT`
  - `SHEETS_POLYMARKET_FIXED_COL_WIDTHS_CATEGORY`

### Polymarket facts（结构化事实，旁路子表，可选）

在同一工作簿内新增一个子表（默认名：`Polymarket事件`），展示 **最近 24h** 的结构化事实（JSONL），用于审计追溯与快速排障。

- `SHEETS_TAB_POLYMARKET_EVENTS`：子表名称（默认 `Polymarket事件`）
- `SHEETS_POLYMARKET_FACTS_EVENTS_ENABLE`：`0/1`（默认 `1`）
- `SHEETS_POLYMARKET_FACTS_EVENTS_INTERVAL_SECONDS`：最小刷新间隔（默认 900）
- `SHEETS_POLYMARKET_FACTS_MODE`：`auto|local|ssh`（默认 `auto`；优先 ssh）
- `SHEETS_POLYMARKET_FACTS_DIR`：facts 根目录（默认 `$HOME/.local/state/tradecat/polymarket/facts`）
- `SHEETS_POLYMARKET_FACTS_EVENTS_LIMIT`：最多展示事件行数（默认 400）
- `SHEETS_POLYMARKET_FACTS_MAX_MB`：最大读取大小（默认 10；防止误扫超大 jsonl）
- `SHEETS_POLYMARKET_FACTS_TIMEOUT_SECONDS`：导出超时（默认 20）
- `SHEETS_PRUNE_KEEP_POLYMARKET_EVENTS`：`0/1`（默认 `1`；`--prune-tabs` 时保留该 tab）

### 币种查询子表（真表格）版式说明

当前“币种查询”不再写入整段 TXT，而是写入可筛选/可排序的结构化表格（复用主看板方案5的设计思路）：

- 冻结：前 4 行（元信息/说明/目录/全局表头）+ 左侧 3 列（面板/指标组/指标）
- 全局表头只出现一次：`面板 | 指标组 | 指标 | 1m..1w | 原始值 | 1m..1w(raw)`
- 面板列（A）按块纵向合并并交替底色；指标组列（B）按块纵向合并
- `SHEETS_HIDE_PERIODS` 会从展示表里“删除周期列”（默认删除 `1m`）
- `SHEETS_SYMBOL_QUERY_RAW_MODE` 控制是否追加 raw 镜像区（默认 `off`，避免出现 J..P 等额外列；如需启用：`hidden|show`）
- 列宽（可选覆盖）：`SHEETS_SYMBOL_QUERY_COL_WIDTH_PANEL/GROUP/METRIC/PERIOD`（默认更紧凑）

## 运行

```bash
cd plugins/sheets
make install
make run          # 前台跑一次
make start        # 后台 daemon
make status
```

## SA 模式：全 CLI 初始化工作簿

```bash
cd plugins/sheets
export SHEETS_WRITE_MODE=sa
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/sa-key.json"
.venv/bin/python -m src --bootstrap --bootstrap-title "TradeCat TG Cards Dashboard"
```

输出里会给你 `spreadsheet_id` 与链接；把 `spreadsheet_id` 写入 `SHEETS_SPREADSHEET_ID` 后即可正常同步：

```bash
export SHEETS_SPREADSHEET_ID="..."
.venv/bin/python -m src --once --cards super_trend_ranking,macd_ranking,bb_ranking
```

版式/样式更新后需要强制刷新（忽略幂等）：

```bash
.venv/bin/python -m src --once --force
```

仅发布 `API` 目录表（不跑全量刷新；便于对外消费验收）：

```bash
cd plugins/sheets
export SHEETS_WRITE_MODE=sa
export SHEETS_SPREADSHEET_ID="..."
.venv/bin/python -m src --publish-api-table
```

## 本地验收（无需 Google）

启动 mock webhook：

```bash
cd plugins/sheets
.venv/bin/python -m src --mock-webhook --mock-port 18080
```

另一个终端发送（dry-run 写 outbox + flush）：

```bash
export SHEETS_WEBHOOK_URL="http://127.0.0.1:18080/exec"
export SHEETS_WEBHOOK_SECRET="dev-secret"
cd plugins/sheets
.venv/bin/python -m src --once --cards super_trend_ranking,macd_ranking,bb_ranking
```
