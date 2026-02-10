#!/usr/bin/env node
/**
 * 实时信号监控终端
 * 
 * 使用真实数据源测试 7 个新信号模块
 * 运行: node live-signal-monitor.js
 */

// 全局代理注入 - 必须在最开头
const fs = require('fs');
const path = require('path');
const resolveProjectRoot = () => {
    const explicitRoot = process.env.TRADECAT_ROOT || process.env.PROJECT_ROOT;
    if (explicitRoot) {
        return explicitRoot;
    }
    let current = __dirname;
    while (true) {
        const dotenvCandidate = path.join(current, 'config', '.env');
        if (fs.existsSync(dotenvCandidate)) {
            return current;
        }
        const parent = path.dirname(current);
        if (parent === current) {
            return process.cwd();
        }
        current = parent;
    }
};
const projectRoot = resolveProjectRoot();
const dotenvPath = path.join(projectRoot, 'config', '.env');
require('dotenv').config({ path: dotenvPath, override: true });
const { bootstrap } = require('global-agent');
const monitorProxy = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.OPINION_PROXY || process.env.DEFAULT_PROXY_URL;
if (monitorProxy && !process.env.GLOBAL_AGENT_HTTP_PROXY) {
    process.env.GLOBAL_AGENT_HTTP_PROXY = monitorProxy;
}
if (monitorProxy && !process.env.GLOBAL_AGENT_HTTPS_PROXY) {
    process.env.GLOBAL_AGENT_HTTPS_PROXY = monitorProxy;
}
if (process.env.GLOBAL_AGENT_HTTP_PROXY || process.env.GLOBAL_AGENT_HTTPS_PROXY) {
    bootstrap();
}

const fetch = require('node-fetch');

// 检测器
const PriceSpikeDetector = require('./signals/price-spike/detector');
const LargeTradeDetector = require('./signals/whale/detector');
const NewMarketDetector = require('./signals/new-market/detector');
const DeepArbDetector = require('./signals/deep-arb/detector');
const LiquidityAlertDetector = require('./signals/liquidity-alert/detector');
const BookSkewDetector = require('./signals/book-skew/detector');
const SmartMoneyDetector = require('./signals/smart-money/detector');

// 格式化器
const { formatPriceSpikeSignal } = require('./signals/price-spike/formatter');
const { formatLargeTradeSignal } = require('./signals/whale/formatter');
const { formatNewMarketSignal } = require('./signals/new-market/formatter');
const { formatDeepArbSignal } = require('./signals/deep-arb/formatter');
const { formatLiquidityAlertSignal } = require('./signals/liquidity-alert/formatter');
const { formatBookSkewSignal } = require('./signals/book-skew/formatter');
const { formatSmartMoneySignal } = require('./signals/smart-money/formatter');

// 颜色
const colors = {
    reset: '\x1b[0m',
    bright: '\x1b[1m',
    red: '\x1b[31m',
    green: '\x1b[32m',
    yellow: '\x1b[33m',
    blue: '\x1b[34m',
    magenta: '\x1b[35m',
    cyan: '\x1b[36m',
    white: '\x1b[37m',
    gray: '\x1b[90m'
};

// 统计
const stats = {
    messages: 0,
    signals: { priceSpike: 0, largeTrade: 0, newMarket: 0, deepArb: 0, liquidityAlert: 0, bookSkew: 0, smartMoney: 0 },
    startTime: Date.now()
};

// 市场元数据缓存
const marketCache = new Map();
const CLOB_API_BASE = process.env.CLOB_API_BASE || process.env.POLYMARKET_CLOB_API_BASE || '';
const GAMMA_API_BASE = process.env.GAMMA_API_BASE || process.env.POLYMARKET_GAMMA_API_BASE || process.env.NEW_MARKET_GAMMA_API || '';
const DATA_API_BASE = process.env.DATA_API_BASE || process.env.POLYMARKET_DATA_API_BASE || process.env.SMART_MONEY_DATA_API || '';

// 初始化检测器 (降低阈值以便测试)
const detectors = {
    // 价格突变：放宽窗口，启用冷却
    priceSpike: new PriceSpikeDetector({
        minChange: 0.04,            // 至少 4% 变化
        windowMs: 45000,            // 45s 窗口
        cooldown: 60000,            // 单市场 60s 冷却
        maxSignalsPerHour: 100000,   // 不做全局限频
        disableRateLimit: false
    }),

    // 大额交易：提高阈值并启用冷却
    largeTrade: new LargeTradeDetector({
        minValue: 20000,            // $20K 起报
        cooldown: 30000,
        maxSignalsPerHour: 100000,
        disableRateLimit: false
    }),

    newMarket: new NewMarketDetector({
        maxAge: 3600000,
        maxSignalsPerHour: 100000,
        disableRateLimit: false
    }),

    // 深度套利：保持灵敏，但启用冷却
    deepArb: new DeepArbDetector({
        minProfit: 0.004,           // 0.4%
        minDepth: 100,              // $100
        cooldown: 60000,
        maxSignalsPerHour: 100000,
        disableRateLimit: false
    }),

    // 流动性枯竭：收紧阈值、增大窗口、启用冷却
    liquidityAlert: new LiquidityAlertDetector({
        dropThreshold: 0.5,         // 深度下降 ≥50%
        minDepth: 3000,             // 至少 $3K 深度
        windowMs: 45000,            // 对比 45s
        cooldown: 180000,           // 单市场 3 分钟
        maxSignalsPerHour: 100000,
        disableRateLimit: false
    }),

    // 订单簿倾斜：收紧比例、深度、窗口与冷却
    bookSkew: new BookSkewDetector({
        minSkewChange: 0.6,         // 倾斜变化 ≥60%
        minDepth: 3000,             // 至少 $3K 深度
        windowMs: 45000,            // 对比 45s
        cooldown: 180000,           // 单市场 3 分钟
        maxSignalsPerHour: 100000,
        disableRateLimit: false
    }),

    // 聪明钱：提高持仓价值门槛
    smartMoney: new SmartMoneyDetector({
        trackTopN: 10,
        minPositionValue: 2000,     // $2K 以上才告警
        scanIntervalMs: 120000,
        maxSignalsPerHour: 100000,
        disableRateLimit: false
    })
};

// 新市场基线标记
let newMarketBaselineLoaded = false;

function log(color, prefix, message) {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    console.log(`${colors.gray}${time}${colors.reset} ${color}${prefix}${colors.reset} ${message}`);
}

function logSignal(type, signal, formatted) {
    console.log('\n' + '─'.repeat(60));
    log(colors.bright + colors.green, `🎯 [${type}]`, '检测到信号!');
    
    // 简化输出 (移除 Markdown)
    const text = formatted.text
        .replace(/\*\*/g, '')
        .replace(/\*/g, '')
        .replace(/```/g, '')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
    
    console.log(text);
    console.log('─'.repeat(60) + '\n');
}

function printStats() {
    const uptime = Math.floor((Date.now() - stats.startTime) / 1000);
    const mins = Math.floor(uptime / 60);
    const secs = uptime % 60;
    
    process.stdout.write(`\r${colors.gray}[${mins}:${secs.toString().padStart(2, '0')}] ` +
        `消息: ${stats.messages} | ` +
        `💸${stats.signals.largeTrade} ` +
        `🆕${stats.signals.newMarket} ` +
        `🧠${stats.signals.smartMoney}${colors.reset}`);
}

async function fetchMarketMeta(conditionId) {
    if (marketCache.has(conditionId)) {
        return marketCache.get(conditionId);
    }
    
    try {
        const response = await fetch(`${CLOB_API_BASE}/markets/${conditionId}`);
        if (response.ok) {
            const data = await response.json();
            const meta = {
                conditionId,
                slug: data.market_slug,
                eventSlug: data.event_slug,
                question: data.question,
                tokens: data.tokens || []
            };
            marketCache.set(conditionId, meta);
            return meta;
        }
    } catch (e) {}
    
    return { conditionId };
}

function normalizeEvent(raw) {
    if (!raw) return null;
    
    // 旧格式: ["price_change", {...}]
    if (Array.isArray(raw) && raw.length === 2 && typeof raw[0] === 'string') {
        return { type: raw[0], payload: raw[1] };
    }
    
    // WebSocket ACK/通用格式: { type: 'xxx', data: {...} }
    if (raw.type && raw.data) {
        return { type: raw.type, payload: raw.data };
    }
    
    // Polymarket 现行格式: { event_type: 'book' | 'trade' | 'price_change', ... }
    if (raw.event_type) {
        return { type: raw.event_type, payload: raw };
    }
    
    return null;
}

async function processMessage(type, payload) {
    stats.messages++;
    
    if (!type || !payload) return;

    // 提取市场信息
    const conditionId = payload.market || payload.condition_id || payload.conditionId;
    const assetId = payload.asset_id || payload.asset;
    
    let meta = {};
    if (conditionId) {
        meta = await fetchMarketMeta(conditionId);
    }

    // 1. 价格突变 (已禁用)
    // if (type === 'price_change' && payload.price) { ... }

    // 2. 大额交易 (trade 消息)
    if (type === 'trade' || (payload.size && payload.price && payload.side)) {
        const signal = detectors.largeTrade.process({
            assetId: assetId,
            price: parseFloat(payload.price),
            side: payload.side,
            size: parseFloat(payload.size),
            timestamp: Date.now()
        }, meta);
        
        if (signal) {
            stats.signals.largeTrade++;
            logSignal('大额交易', signal, formatLargeTradeSignal(signal));
        }
    }

    // 3. 订单簿相关 (已禁用 - 深度套利、流动性枯竭、订单簿倾斜)
    // if (type === 'book' && payload.bids && payload.asks) {
    //     ...
    // }
}

async function scanNewMarkets() {
    const isBaseline = !newMarketBaselineLoaded;
    if (isBaseline) {
        log(colors.cyan, '[扫描]', '加载新市场基线...');
    } else {
        log(colors.cyan, '[扫描]', '检查新市场...');
    }
    
    try {
        const response = await fetch(`${GAMMA_API_BASE}/markets?active=true&closed=false&limit=500&order=createdAt&ascending=false`);
        if (!response.ok) return;
        
        const markets = await response.json();
        
        for (const market of markets) {
            const signal = detectors.newMarket.process(market);
            // 只有基线加载后才推送信号
            if (signal && !isBaseline) {
                stats.signals.newMarket++;
                logSignal('新市场', signal, formatNewMarketSignal(signal));
            }
        }
        
        if (isBaseline) {
            newMarketBaselineLoaded = true;
            log(colors.green, '[扫描]', `基线加载完成，已记录 ${detectors.newMarket.seenMarkets.size} 个市场`);
        }
    } catch (e) {
        log(colors.red, '[错误]', `新市场扫描失败: ${e.message}`);
    }
}

// 聪明钱持仓快照 { address -> { conditionId -> { size, side, value } } }
const smartMoneySnapshot = new Map();
let smartMoneyBaselineLoaded = false;

async function scanSmartMoney() {
    const isBaseline = !smartMoneyBaselineLoaded;
    log(colors.cyan, '[扫描]', isBaseline ? '加载聪明钱基线...' : '检查聪明钱...');
    
    try {
        // 获取排行榜 Top 100
        const response = await fetch(`${DATA_API_BASE}/v1/leaderboard?limit=100`);
        if (!response.ok) return;
        
        const data = await response.json();
        const traders = Array.isArray(data) ? data : (data.leaderboard || data || []);
        
        for (const trader of traders) {
            const address = trader.proxyWallet || trader.address;
            if (!address) continue;
            
            // 获取持仓
            const posResponse = await fetch(`${DATA_API_BASE}/positions?user=${address}&limit=50`);
            if (!posResponse.ok) continue;
            
            const positions = await posResponse.json();
            const oldSnapshot = smartMoneySnapshot.get(address) || new Map();
            const newSnapshot = new Map();
            
            for (const pos of (positions || [])) {
                const value = (pos.size || 0) * (pos.curPrice || 0);
                if (value < 100) continue; // 忽略小仓位
                
                const key = pos.conditionId || pos.asset;
                newSnapshot.set(key, {
                    size: pos.size || 0,
                    side: pos.outcome || pos.side,
                    value,
                    title: pos.title || pos.question,
                    curPrice: pos.curPrice
                });
                
                // 检测变化（非基线模式）
                if (!isBaseline) {
                    const old = oldSnapshot.get(key);
                    if (!old) {
                        // 新建仓
                        stats.signals.smartMoney++;
                        log(colors.magenta, '[🧠 新建仓]', `#${trader.rank} ${pos.outcome || ''} $${value.toFixed(0)} | ${(pos.title || '').slice(0, 40)}`);
                    } else if (value > old.value * 1.5) {
                        // 加仓 >50%
                        stats.signals.smartMoney++;
                        log(colors.magenta, '[🧠 加仓]', `#${trader.rank} +${((value/old.value - 1) * 100).toFixed(0)}% → $${value.toFixed(0)} | ${(pos.title || '').slice(0, 40)}`);
                    }
                }
            }
            
            // 检测清仓（非基线模式）
            if (!isBaseline) {
                for (const [key, old] of oldSnapshot) {
                    if (!newSnapshot.has(key) && old.value > 500) {
                        stats.signals.smartMoney++;
                        log(colors.yellow, '[🧠 清仓]', `#${trader.rank} $${old.value.toFixed(0)} | ${(old.title || '').slice(0, 40)}`);
                    }
                }
            }
            
            smartMoneySnapshot.set(address, newSnapshot);
        }
        
        if (isBaseline) {
            smartMoneyBaselineLoaded = true;
            log(colors.green, '[扫描]', `聪明钱基线加载完成，跟踪 ${smartMoneySnapshot.size} 个地址`);
        }
    } catch (e) {
        log(colors.red, '[错误]', `聪明钱扫描失败: ${e.message}`);
    }
}

async function connectWebSocket() {
    log(colors.blue, '[WS]', '连接 Polymarket WebSocket...');
    
    const WebSocket = require('ws');
    const { HttpsProxyAgent } = require('https-proxy-agent');
    const proxyAgent = new HttpsProxyAgent(process.env.GLOBAL_AGENT_HTTP_PROXY);
    
    const ws = new WebSocket('wss://ws-subscriptions-clob.polymarket.com/ws/market', { agent: proxyAgent });
    
    ws.on('open', () => {
        log(colors.green, '[WS]', '连接成功!');
        
        // 订阅热门市场
        subscribeToMarkets(ws);
    });
    
    ws.on('message', async (data) => {
        try {
            const parsed = JSON.parse(data.toString());
            const events = Array.isArray(parsed) ? parsed : [parsed];
            
            for (const raw of events) {
                const evt = normalizeEvent(raw);
                if (!evt) continue;
                await processMessage(evt.type, evt.payload);
            }
        } catch (e) {
            log(colors.red, '[WS]', `消息解析失败: ${e.message}`);
        }
        
        printStats();
    });
    
    ws.on('error', (error) => {
        log(colors.red, '[WS]', `错误: ${error.message}`);
    });
    
    ws.on('close', () => {
        log(colors.yellow, '[WS]', '连接断开，5秒后重连...');
        setTimeout(connectWebSocket, 5000);
    });
    
    return ws;
}

async function subscribeToMarkets(ws) {
    try {
        const assetIds = [];
        let offset = 0;
        
        // 分页获取全部活跃市场
        while (true) {
            const response = await fetch(`${GAMMA_API_BASE}/markets?active=true&closed=false&limit=500&offset=${offset}`);
            if (!response.ok) break;
            
            const markets = await response.json();
            if (!markets.length) break;
            
            for (const market of markets) {
                if (market.clobTokenIds) {
                    const ids = typeof market.clobTokenIds === 'string' ? JSON.parse(market.clobTokenIds) : market.clobTokenIds;
                    if (Array.isArray(ids)) assetIds.push(...ids);
                }
                if (market.conditionId) {
                    marketCache.set(market.conditionId, {
                        conditionId: market.conditionId,
                        slug: market.slug,
                        question: market.question,
                        tokens: market.tokens
                    });
                }
            }
            
            if (markets.length < 500) break;
            offset += 500;
        }
        
        if (assetIds.length > 0) {
            ws.send(JSON.stringify({
                type: 'subscribe',
                channel: 'market',
                assets_ids: assetIds
            }));
            
            log(colors.green, '[WS]', `已订阅 ${assetIds.length} 个资产 (${marketCache.size} 个市场)`);
        }
    } catch (e) {
        log(colors.red, '[错误]', `订阅失败: ${e.message}`);
    }
}

async function main() {
    console.clear();
    console.log(`
${colors.bright}${colors.cyan}╔════════════════════════════════════════════════════════════╗
║           🔍 Polymarket 实时信号监控终端                    ║
╠════════════════════════════════════════════════════════════╣
║  📈 价格突变  💸 大额交易  🆕 新市场  ⚡ 深度套利          ║
║  🚨 流动性枯竭  📊 订单簿倾斜  🧠 聪明钱                   ║
╚════════════════════════════════════════════════════════════╝${colors.reset}
`);

    // 连接 WebSocket
    await connectWebSocket();
    
    // 定时扫描新市场 (每分钟)
    setInterval(scanNewMarkets, 60000);
    setTimeout(scanNewMarkets, 5000);
    
    // 定时扫描聪明钱 (每2分钟)
    setInterval(scanSmartMoney, 120000);
    setTimeout(scanSmartMoney, 10000);
    
    // 定时打印统计
    setInterval(printStats, 1000);
    
    log(colors.green, '[启动]', '监控已启动，按 Ctrl+C 退出');
}

main().catch(console.error);
