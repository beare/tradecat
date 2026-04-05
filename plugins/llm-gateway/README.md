# llm-gateway

定位：作为 `nofx` 的 **外部“提示词组装器/决策网关”**，实现 OpenAI 兼容的 `POST /v1/chat/completions`。

nofx 侧只需要把模型 `custom_api_url` 指向本服务（例如 `http://127.0.0.1:9010/v1`），并把 `api_key` 设置为 `LLM_GATEWAY_TOKEN`，即可在不改 nofx 代码的前提下接入：

```text
nofx -> (OpenAI chat/completions) -> llm-gateway -> (真实模型) -> llm-gateway -> nofx
```

## 契约（nofx 解析要求）

本服务返回的 `choices[0].message.content` 必须包含：

```text
<reasoning>...</reasoning>
<decision>[{...}]</decision>
```

其中 `<decision>` 必须是 JSON 数组，并满足 nofx 的硬约束：
- 必须以 `[{` 开头（允许空白）
- 禁止 `~`（范围值）
- 禁止千分位逗号（例如 `1,234`）

## 配置（环境变量）

说明：`src/config.py` 会自动定位项目根目录，并优先加载共享配置：
- `<repo>/assets/config/.env`（只读，不提交；推荐唯一真相源）
- `<repo>/config/.env`（兼容旧路径）

必填：
- `LLM_GATEWAY_TOKEN`：网关鉴权 token（nofx 通过 `Authorization: Bearer <token>` 传入）

服务端口：
- `LLM_GATEWAY_HOST`（默认 `0.0.0.0`）
- `LLM_GATEWAY_PORT`（默认 `9010`）
- `LLM_GATEWAY_DEBUG`（默认 `false`）

上游模型（可选，未配置则安全降级 `wait`）：
- `LLM_GATEWAY_UPSTREAM_BASE_URL`：上游 OpenAI-compatible base_url（例如 DashScope 的 `/compatible-mode/v1`）
  - 约定：末尾加 `#` 表示“完整 URL”（不再追加 `/chat/completions`）
- `LLM_GATEWAY_UPSTREAM_API_KEY`：上游 API key
- `LLM_GATEWAY_UPSTREAM_MODEL`：默认 model（当请求体 `model` 为空时使用）
- `LLM_GATEWAY_UPSTREAM_TIMEOUT_SECONDS`（默认 `30`）
- `LLM_GATEWAY_UPSTREAM_ALLOWLIST`：可选域名/主机 allowlist（逗号分隔；支持 `.example.com` 后缀匹配）
- `LLM_GATEWAY_UPSTREAM_REQUIRE_HTTPS`：是否强制 https（默认 `true`）
- `LLM_GATEWAY_UPSTREAM_BLOCK_PRIVATE`：是否禁止私网/回环/链路本地（默认 `true`；如需本地推理需显式关闭）

Tradecat 数据出口（可选）：
- `QUERY_SERVICE_BASE_URL`：Query Service 基地址（例如 `http://127.0.0.1:8088`）
- `QUERY_SERVICE_TOKEN`：内部鉴权 token（Header: `X-Internal-Token`）

资产落盘：
- `LLM_GATEWAY_RUN_ASSETS_DIR`：run_asset 根目录（默认 `plugins/llm-gateway/data/run_asset`）
- `LLM_GATEWAY_RUN_ASSETS_ENABLED`：是否启用落盘（默认 `true`）

请求上限（防 DoS）：
- `LLM_GATEWAY_MAX_BODY_BYTES`：请求体最大字节数（默认 `1048576`）
- `LLM_GATEWAY_MAX_MESSAGES`：messages 最大条数（默认 `32`）
- `LLM_GATEWAY_MAX_MESSAGE_CHARS`：单条 message 最大字符数（默认 `200000`）

## 运行

```bash
cd plugins/llm-gateway
make install-dev
make run
```

后台：
```bash
make start
make status
```

## 运维（run_asset 治理）

查看落盘占用：
```bash
du -sh plugins/llm-gateway/data/run_asset 2>/dev/null || true
```

按 TTL/数量上限清理：
```bash
make prune-assets
```
