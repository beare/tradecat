/**
 * Market Channel 事件适配回归测试（离线）
 *
 * 背景：
 * - Polymarket 已将 CLOB 行情推送迁移到 `ws-subscriptions-clob ... /ws/market`
 * - 该通道消息结构与旧的 topic/type/payload 不同
 * - bot 侧通过 `normalizeMarketChannelEvent()` 适配为旧结构，复用既有检测器
 *
 * 本测试不依赖网络：仅验证适配层的输入输出结构。
 */

const assert = require('assert');
const PolymarketSignalBot = require('../bot');
const baseConfig = require('../config/settings');

// 测试必须完全离线：禁用 Telegram 初始化/轮询，避免 hang 住进程
process.env.POLYMARKET_DISABLE_TELEGRAM_POLLING = '1';
const config = JSON.parse(JSON.stringify(baseConfig));
config.telegram = { ...(config.telegram || {}), token: '' };

const bot = new PolymarketSignalBot(config);

// 注入最小元信息映射：asset_id -> {market,outcome,slug,title}
bot.marketChannelAssetMeta = new Map([
    ['A_YES', { market: 'M1', outcome: 'Yes', slug: 'm1-slug', title: 'Market 1' }],
    ['A_NO', { market: 'M1', outcome: 'No', slug: 'm1-slug', title: 'Market 1' }]
]);

// 1) book 事件
{
    const raw = {
        market: 'M1',
        asset_id: 'A_YES',
        bids: [{ price: '0.40', size: '100' }],
        asks: [{ price: '0.60', size: '200' }],
        timestamp: '0'
    };
    const out = bot.normalizeMarketChannelEvent(raw);
    assert.strictEqual(out.length, 1);
    assert.strictEqual(out[0].topic, 'clob_market');
    assert.strictEqual(out[0].type, 'agg_orderbook');
    assert.strictEqual(out[0].payload.market, 'M1');
    assert.strictEqual(out[0].payload.asset_id, 'A_YES');
    assert.strictEqual(out[0].payload.outcome, 'Yes');
    assert.deepStrictEqual(out[0].payload.token, { outcome: 'Yes' });
    assert.strictEqual(out[0].payload.slug, 'm1-slug');
    assert.strictEqual(out[0].payload.title, 'Market 1');
}

// 2) price_change (legacy schema)
{
    const raw = {
        market: 'M1',
        price_changes: [
            { asset_id: 'A_YES', best_bid: '0.39', best_ask: '0.61', side: 'BUY', size: '1', price: '0.61' },
            { asset_id: 'A_NO', best_bid: '0.59', best_ask: '0.41', side: 'SELL', size: '2', price: '0.41' }
        ]
    };
    const out = bot.normalizeMarketChannelEvent(raw);
    assert.strictEqual(out.length, 1);
    assert.strictEqual(out[0].topic, 'clob_market');
    assert.strictEqual(out[0].type, 'price_change');
    assert.strictEqual(out[0].payload.m, 'M1');
    assert(Array.isArray(out[0].payload.pc));
    assert.strictEqual(out[0].payload.pc.length, 2);
    assert.deepStrictEqual(out[0].payload.pc[0], {
        a: 'A_YES',
        ba: '0.61',
        bb: '0.39',
        s: 'BUY',
        p: '0.61',
        sz: '1',
        o: 'Yes'
    });
    assert.deepStrictEqual(out[0].payload.pc[1].o, 'No');
}

// 3) last_trade_price / trade
{
    const raw = {
        market: 'M1',
        asset_id: 'A_NO',
        price: '0.41',
        size: '5000',
        side: 'BUY',
        timestamp: '0'
    };
    const out = bot.normalizeMarketChannelEvent(raw);
    assert.strictEqual(out.length, 1);
    assert.strictEqual(out[0].topic, 'activity');
    assert.strictEqual(out[0].type, 'trades');
    assert.strictEqual(out[0].payload.asset_id, 'A_NO');
    assert.strictEqual(out[0].payload.token_id, 'A_NO');
    assert.strictEqual(out[0].payload.outcome, 'No');
    assert.deepStrictEqual(out[0].payload.token, { outcome: 'No' });
}

console.log('✅ Market Channel 适配层测试通过');
