/**
 * 大额交易信号格式化器
 */

const { t } = require('../../i18n');
const POLYMARKET_WEB_BASE = (process.env.POLYMARKET_WEB_BASE || '').replace(/\/$/, '');

function buildMarketUrl(signal) {
    const slug = signal.eventSlug || signal.marketSlug;
    return slug && POLYMARKET_WEB_BASE ? `${POLYMARKET_WEB_BASE}/event/${slug}` : null;
}

function formatAmount(value) {
    if (!Number.isFinite(value)) return 'N/A';
    if (value >= 1000000) return `$${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `$${(value / 1000).toFixed(1)}K`;
    return `$${value.toFixed(0)}`;
}

function getCurrentTime() {
    return new Date().toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatLargeTradeSignal(signal, options = {}) {
    const lang = options.lang || 'zh-CN';
    const i18n = t(lang);
    
    const { side, outcome, price, value, marketName } = signal;

    const emoji = side === 'BUY' ? '🟢' : '🔴';
    const action = side === 'BUY' ? i18n.whale.buy : i18n.whale.sell;
    const priceStr = ((price || 0) * 100).toFixed(1);
    const time = getCurrentTime();
    const link = buildMarketUrl(signal);

    const codeLines = [
        `${emoji} ${action} ${outcome || 'YES'} @ ${priceStr}¢`,
        `💰 ${i18n.whale.value} ${formatAmount(value)}`
    ];

    // 交易者地址
    if (signal.traderAddress) {
        const addr = signal.traderAddress;
        codeLines.push(`👤 ${addr.slice(0, 6)}...${addr.slice(-4)}`);
    }

    const codeBlock = ['```', ...codeLines, '```'].join('\n');

    const text = [
        `🏷️ ${marketName || i18n.unknownMarket}`,
        codeBlock,
        `⏱️ ${time} ${i18n.whale.title}`
    ].join('\n');

    return {
        text,
        keyboard: link ? { inline_keyboard: [[{ text: i18n.openMarket, url: link }]] } : undefined,
        translationTargets: marketName ? [{ text: marketName, conditionId: signal.conditionId }] : []
    };
}

// 兼容旧名称
const formatWhaleSignal = formatLargeTradeSignal;

module.exports = { formatLargeTradeSignal, formatWhaleSignal, buildMarketUrl, formatAmount, getCurrentTime };
