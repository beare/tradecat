# Telegram Service

`plugins/telegram` 是 Telegram 输出侧服务：主 Bot 负责交互，导出器负责把 Query Service 的事件/信号推送到频道。

## 功能

| 功能 | 说明 |
|:---|:---|
| **主 Bot** | 排行榜卡片、单币快照、信号订阅、AI 辅助分析 |
| **publisher 导出器** | Query 事件流 → Telegram 频道（JSON） |
| **signal 导出器** | Query 信号事件 → Telegram 频道（人类可读文本） |

## 目录结构

```text
plugins/telegram/
├── scripts/
│   ├── start.sh                  # 主 Bot 生命周期脚本
│   └── exporters.sh              # publisher / signal 生命周期脚本
├── src/
│   ├── bot/                      # 主 Bot 交互逻辑
│   ├── publishers/               # 事件/信号导出器
│   ├── cards/                    # 卡片渲染
│   ├── signals/                  # 信号订阅适配
│   └── path_setup.py             # 路径与共享 .env 加载
├── tests/
├── README.md
└── AGENTS.md
```

## 快速开始

### 环境要求

- Python >= 3.12
- Query Service 已运行（`QUERY_SERVICE_BASE_URL` → `/api/v1`）

### 安装

```bash
cd plugins/telegram
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置

```bash
cp assets/config/.env.example assets/config/.env
chmod 600 assets/config/.env
vim assets/config/.env
```

主 Bot 至少需要：

- `BOT_TOKEN`
- `QUERY_SERVICE_BASE_URL`
- `QUERY_SERVICE_TOKEN`（当 Query 鉴权开启时）

### 启动主 Bot

```bash
cd plugins/telegram
./scripts/start.sh start
./scripts/start.sh status
```

### 启动导出器

```bash
cd plugins/telegram

# 事件 JSON -> Telegram
./scripts/exporters.sh publisher-start
./scripts/exporters.sh publisher-status

# 信号文本 -> Telegram
./scripts/exporters.sh signal-start
./scripts/exporters.sh signal-status
```

## 配置说明

### 主 Bot

| 变量 | 必填 | 说明 |
|:---|:---:|:---|
| `BOT_TOKEN` | ✓ | 主 Bot Token |
| `QUERY_SERVICE_BASE_URL` | ✓ | Query Service 基地址 |
| `QUERY_SERVICE_TOKEN` | - | Query Service 内网 token |
| `QUERY_SERVICE_TIMEOUT_SECONDS` | - | Query 请求超时（秒） |
| `QUERY_SERVICE_CACHE_TTL_SECONDS` | - | Query 请求缓存 TTL（秒） |
| `HTTP_PROXY` / `HTTPS_PROXY` | - | 代理地址 |
| `DEFAULT_LOCALE` | - | 默认语言 |
| `SUPPORTED_LOCALES` | - | 支持语言列表 |
| `FALLBACK_LOCALE` | - | 缺失翻译时回退语言 |

### publisher / signal 导出器

| 变量 | 必填 | 说明 |
|:---|:---:|:---|
| `TG_PUBLISHER_CHANNEL_ID` | - | publisher 目标频道；为空时回退 `TG_CHANNEL_ID` |
| `TG_PUBLISHER_BOT_TOKENS` | - | publisher Bot tokens；为空时回退 `BOT_TOKEN` |
| `TG_PUBLISHER_EXCLUDE_TAGS` | - | publisher 排除标签，默认 `日历,交易` |
| `TG_PUBLISHER_POLL_INTERVAL` | - | publisher 轮询间隔秒，默认 `1` |
| `TG_SIGNAL_CHANNEL_ID` | - | signal 目标频道；为空时回退 `TG_CHANNEL_ID` |
| `TG_SIGNAL_BOT_TOKENS` | - | signal Bot tokens；为空时回退 `BOT_TOKEN` |
| `TG_SIGNAL_INCLUDE_TAGS` | - | signal 只推送这些标签，默认 `信号` |
| `TG_SIGNAL_POLL_INTERVAL` | - | signal 轮询间隔秒，默认 `1` |

## 数据流

```text
主 Bot
  Query Service (/api/v1)
        │
        ▼
  cards/data_provider.py
        │
        ▼
  Telegram Bot

publisher
  Query Service /api/v1/events
        │
        ▼
  JSON 格式化
        │
        ▼
  Telegram 频道

signal
  Query Service /api/v1/events
        │
        ▼
  信号文本格式化
        │
        ▼
  Telegram 频道
```

## 日志与状态

```bash
tail -f logs/bot.log
tail -f logs/publisher.log
tail -f logs/signal.log
```

运行时状态：

- 主 Bot PID：`pids/bot.pid`
- publisher PID：`pids/publisher.pid`
- signal PID：`pids/signal.pid`
- publisher 游标：`data/publishers/publisher_cursor.json`
- signal 游标：`data/publishers/signal_cursor.json`

## 常见问题

### Bot polling 冲突

```text
telegram.error.Conflict: terminated by other getUpdates request
```

说明同一个 `BOT_TOKEN` 被其它实例占用；先停掉重复实例，再启动本服务。

### 导出器启动失败

至少检查：

1. `QUERY_SERVICE_BASE_URL`
2. `QUERY_SERVICE_TOKEN`（若鉴权开启）
3. `TG_PUBLISHER_CHANNEL_ID` / `TG_SIGNAL_CHANNEL_ID`
4. `TG_PUBLISHER_BOT_TOKENS` / `TG_SIGNAL_BOT_TOKENS`

### 数据读取为空

1. 确认 Query Service 可用：`curl http://127.0.0.1:8088/api/v1/health`
2. 确认上游写库服务已运行
3. 查看 `logs/*.log` 中的 Query 请求错误（超时/401/网络）
