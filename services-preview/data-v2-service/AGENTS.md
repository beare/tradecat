# data-v2-service（预览）- 架构说明

目标：用 **REST/WS 两种采集方式**，基于“工具适配器”（如 `CCXT.py`）采集行情数据，并构造 **全字段并集**（union-of-fields），为后续落库/回放提供稳定的字段空间。

## 目录结构

```
data-v2-service/
├── scripts/start.sh                # 后台启动/停止/状态
└── src/
    ├── __main__.py                 # 入口：选择 REST/WS，输出字段并集观测结果
    ├── config.py                   # 配置：交易所/币种/代理
    └── marketdata/
        ├── union.py                # 全字段并集（唯一真相源）
        ├── runner.py               # 编排：选择适配器并运行采集
        ├── types.py                # Raw/Union 事件结构
        ├── REST/                   # 采集方式：REST
        │   └── CCXT.py             # CCXT REST：尽可能拉取字段
        └── WS/                     # 采集方式：WebSocket
            └── CCXT.py             # CCXT Pro WS：订阅并观测字段
```

## 边界与依赖

- `union.py` 是字段并集的唯一真相源：采集器不负责“字段定义”，只负责“提供样本”。
- `REST/`、`WS/` 是采集方式维度；同一个工具可以在两边各有一个实现文件（例如 `REST/CCXT.py` + `WS/CCXT.py`）。

## 变更记录

- 2026-01-31: 初始化 data-v2-service 骨架（REST/WS + 字段并集）。

