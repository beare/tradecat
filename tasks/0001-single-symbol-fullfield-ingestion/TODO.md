# TODO - 微步骤执行清单

[ ] P0: 选择目标币种与库名（默认 BTCUSDT + market_data_fullfield） | Verify: `printf '%s\n' \"$SYMBOL\" \"$DB\"` | Gate: 两者非空且符合命名规范  
[ ] P0: 确认 TimescaleDB 可连（端口 5434） | Verify: `pg_isready -h localhost -p 5434` | Gate: 输出包含 `accepting connections`  
[ ] P0: 创建新数据库 | Verify: `psql -h localhost -p 5434 -U postgres -d postgres -c \"CREATE DATABASE market_data_fullfield;\"` | Gate: 返回 `CREATE DATABASE` 或已存在的可控报错  
[ ] P0: 对新库执行 DDL（01→09 顺序） | Verify: `for f in services-preview/markets-service/scripts/ddl/*.sql; do psql -h localhost -p 5434 -U postgres -d market_data_fullfield -f \"$f\"; done` | Gate: 全部返回 exit code 0  
[ ] P0: 预检对象存在（raw 表 + quality 函数） | Verify: `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"\\dt raw.*\" | rg -n \"crypto_kline_1m\" && psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"\\df quality.start_batch\"` | Gate: 两条命令均命中  
[ ] P0: 启动 markets-service 单币种 WS 采集（raw 模式） | Verify: `cd services-preview/markets-service && MARKETS_SERVICE_DATABASE_URL=postgresql://postgres:postgres@localhost:5434/market_data_fullfield CRYPTO_WRITE_MODE=raw SYMBOLS_GROUPS=one SYMBOLS_GROUP_one=BTCUSDT python -m src crypto-ws` | Gate: 日志出现“加载 1 个交易对/币种”且无 DB 缺表错误  
[ ] P1: 验证只写入单币种且有数据 | Verify: `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"SELECT COUNT(*) n, COUNT(DISTINCT symbol) uniq FROM raw.crypto_kline_1m;\"` | Gate: `n>0 且 uniq=1`  
[ ] P1: 抽样验证全字段列可见 | Verify: `psql -h localhost -p 5434 -U postgres -d market_data_fullfield -c \"SELECT open_time, close_time, quote_volume, trades, taker_buy_volume, taker_buy_quote_volume FROM raw.crypto_kline_1m ORDER BY open_time DESC LIMIT 5;\"` | Gate: 至少 `open_time/close_time/trades` 非空  
[ ] P1: 若 quote/taker 长期为空，跑一次 backfill 补齐 | Verify: `cd services-preview/markets-service && MARKETS_SERVICE_DATABASE_URL=postgresql://postgres:postgres@localhost:5434/market_data_fullfield CRYPTO_WRITE_MODE=raw SYMBOLS_GROUPS=one SYMBOLS_GROUP_one=BTCUSDT python -m src crypto-backfill --klines --days 2` | Gate: 再次抽样出现至少 1 条 `quote_volume` 或 `taker_buy_volume` 非 NULL  
[ ] P2: （可选）修复 markets-service 强制代理逻辑以支持无代理环境 | Verify: `rg -n \"os\\.environ\\[\\\"HTTP_PROXY\\\"\\]\" -S services-preview/markets-service/src/crypto/config.py` | Gate: 代理仅在显式启用时设置（避免破坏其他服务）  

