# data-v2-service（预览）

目标：基于 **REST / WS 两种采集方式**，使用“工具适配器”（例如 `CCXT.py`）尽可能采集 Binance 行情的 **全字段并集**，并为后续落库/回放留出清晰扩展点。

## 目录结构（关键点）

- `src/marketdata/REST/`：行情采集方式（REST）
- `src/marketdata/WS/`：行情采集方式（WebSocket）
- `src/marketdata/union.py`：全字段并集（唯一真相源）

## 快速开始

```bash
cd services-preview/data-v2-service
make install

# WS 采集（默认 30 秒），需要可用代理（如 7890）
HTTP_PROXY=http://127.0.0.1:7890 HTTPS_PROXY=http://127.0.0.1:7890 make run
```

## 扫描 binanceusdm 端点（ccxt/ccxt.pro）

```bash
# 输出 JSON 报告（REST + WS 方法全集）
services/data-service/.venv/bin/python ./scripts/scan_binanceusdm_ccxt.py --proxy http://127.0.0.1:7890
```

也可以直接运行：

```bash
.venv/bin/python -m src --mode ws --tool ccxt --exchange binanceusdm --symbol BTC/USDT:USDT --seconds 30
```

## 说明

- 本服务默认只做“采集与字段并集构造”，不强绑定数据库。
- 采集结果会在 stdout 输出：每类数据（ticker/trades/orderbook/ohlcv）已观测到的字段集合大小与样例。
