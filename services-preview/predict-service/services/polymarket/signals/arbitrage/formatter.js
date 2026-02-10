/**
 * Telegram消息格式化器 - 套利信号
 *
 * 功能：将套利信号转换为Telegram消息格式
 */

const { t } = require('../../i18n');
const POLYMARKET_WEB_BASE = (process.env.POLYMARKET_WEB_BASE || '').replace(/\/$/, '');

/**
 * 生成进度条
 * @param {number} percent - 百分比 (0-100)
 * @param {number} length - 进度条长度
 * @returns {string}
 */
function generateProgressBar(percent, length = 10) {
    const filled = Math.round((percent / 100) * length);
    const empty = length - filled;
    return '█'.repeat(filled) + '░'.repeat(empty);
}

function getPreferredSlug(signal) {
    return signal.eventSlug || signal.marketSlug || null;
}

function buildMarketUrl(signal) {
    const slug = getPreferredSlug(signal);
    if (!POLYMARKET_WEB_BASE) {
        return null;
    }
    return slug
        ? `${POLYMARKET_WEB_BASE}/event/${slug}`
        : `${POLYMARKET_WEB_BASE}/event/${signal.market}`;
}

/**
 * 格式化套利信号 - 变体1（用户示例优化版）
 */
function formatArbitrageV1(signal) {
    const yesPercent = (signal.yesPrice * 100).toFixed(0);
    const noPercent = (signal.noPrice * 100).toFixed(0);
    const yesBar = generateProgressBar(yesPercent);
    const noBar = generateProgressBar(noPercent);

    const text = `
💰 *套利警报*

🏷️ 市场
${signal.marketName}

📊 价格详情
YES    ${signal.yesPrice.toFixed(2)}  ${yesBar}  ${yesPercent}%
NO     ${signal.noPrice.toFixed(2)}  ${noBar}  ${noPercent}%

合计   ${signal.sum.toFixed(2)}  ← 低于1.0！

💵 收益计算
手续费   -${signal.tradingFeePercent}%
═══════════════
净利润   *${signal.netProfitPercent}%* ✅

✅ 操作指南
1️⃣ 买入 YES @ $${signal.yesPrice.toFixed(3)}
2️⃣ 买入 NO  @ $${signal.noPrice.toFixed(3)}
3️⃣ 锁定利润 $${signal.grossProfit.toFixed(3)}

⏱️ 时效 ${signal.timeWindow / 60}分钟 | ⭐ ${signal.strength}星信号
    `.trim();

    // 使用slug构建正确的URL（如果有的话）
    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [
            [
                { text: '📊 打开市场', url: marketUrl }
            ]
        ]
    };

    return { text, keyboard };
}

/**
 * 格式化套利信号 - 变体2（紧凑版）
 */
function formatArbitrageV2(signal) {
    const yesBar = generateProgressBar(signal.yesPrice * 100);
    const noBar = generateProgressBar(signal.noPrice * 100);

    const text = `
💰 *套利机会*

${signal.marketName}
━━━━━━━━━━━━━━━━

📊 YES ${signal.yesPrice.toFixed(2)} ${yesBar}
📊 NO  ${signal.noPrice.toFixed(2)} ${noBar}
━━━━━━━━━━━━━━━━
    合计 ${signal.sum.toFixed(2)} ⚠️

💵 净利润 *+${signal.netProfitPercent}%*

✅ 买YES ${signal.yesPrice.toFixed(2)} + 买NO ${signal.noPrice.toFixed(2)}
⏱️ ${signal.timeWindow / 60}分钟 | ${'⭐'.repeat(signal.strength)}
    `.trim();

    // 使用slug构建正确的URL（如果有的话）
    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [
            [
                { text: '📊 打开市场', url: marketUrl }
            ]
        ]
    };

    return { text, keyboard };
}

/**
 * 格式化套利信号 - 变体4（表格紧凑版 - 使用代码块）
 */
function formatArbitrageV4(signal) {
    const yesBar = generateProgressBar(signal.yesPrice * 100, 9);
    const noBar = generateProgressBar(signal.noPrice * 100, 9);

    // 如果marketName是完整的conditionId，显示短ID
    let displayName = signal.marketName;
    if (displayName && displayName.startsWith('0x') && displayName.length > 20) {
        displayName = displayName.substring(0, 12) + '...';
    }

    const text = `
💰 *套利警报*

🏷️ ${displayName}
\`\`\`
项目      价格    占比
YES      ${signal.yesPrice.toFixed(2)}    ${yesBar}
NO       ${signal.noPrice.toFixed(2)}    ${noBar}
合计     ${signal.sum.toFixed(2)}    ⚠️ 套利！
\`\`\`
💵 收益
费用 -${signal.tradingFeePercent}%
净利 *${signal.netProfitPercent}%* ✅

✅ 买YES + 买NO = 稳赚
⏱️ ${signal.timeWindow / 60}分钟 | ${'⭐'.repeat(signal.strength)}

🔗 _点击下方"📊 打开市场"按钮查看详情_
    `.trim();

    // 使用slug构建正确的URL（如果有的话）
    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [
            [
                { text: '📊 打开市场', url: marketUrl }
            ]
        ]
    };

    return { text, keyboard };
}

/**
 * 格式化套利信号 - 变体5（极简版）
 */
function formatArbitrageV5(signal) {
    const text = `
💰 套利 | ${signal.marketName}

YES ${signal.yesPrice.toFixed(2)} + NO ${signal.noPrice.toFixed(2)} = ${signal.sum.toFixed(2)}

净利 *${signal.netProfitPercent}%* ✅

⏱️ ${signal.timeWindow / 60}分 | ${'⭐'.repeat(signal.strength)}
    `.trim();

    // 使用slug构建正确的URL（如果有的话）
    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [
            [{ text: '🚀 去交易', url: marketUrl }]
        ]
    };

    return { text, keyboard };
}

/**
 * 新方案A - 使用市场名称（推荐）
 */
function formatArbitrageNewA(signal) {
    const yesBar = generateProgressBar(signal.yesPrice * 100, 9);
    const noBar = generateProgressBar(signal.noPrice * 100, 9);

    // 计算差价百分比
    const gapPercent = ((1.00 - signal.sum) * 100).toFixed(2);

    const text = `
💰 *套利警报*

🏷️ ${signal.marketName || '未知市场'}
\`\`\`
方向      价格    占比
YES      ${signal.yesPrice.toFixed(2)}    ${yesBar}
NO       ${signal.noPrice.toFixed(2)}    ${noBar}
合计     ${signal.sum.toFixed(2)}    差价${gapPercent}%
\`\`\`
    `.trim();

    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [[{ text: '📊 打开市场', url: marketUrl }]]
    };

    return { text, keyboard };
}

/**
 * 新方案B - 使用 slug（简洁版）
 */
function formatArbitrageNewB(signal) {
    const yesBar = generateProgressBar(signal.yesPrice * 100, 9);
    const noBar = generateProgressBar(signal.noPrice * 100, 9);

    const slug = getPreferredSlug(signal) || signal.market.substring(0, 12) + '...';

    const text = `
💰 *套利警报*

🆔 ${slug}
\`\`\`
项目      价格    占比
YES      ${signal.yesPrice.toFixed(2)}    ${yesBar}
NO       ${signal.noPrice.toFixed(2)}    ${noBar}
合计     ${signal.sum.toFixed(2)}    ⚠️ 套利！
\`\`\`
    `.trim();

    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [[{ text: '📊 打开市场', url: marketUrl }]]
    };

    return { text, keyboard };
}

/**
 * 新方案C - 省略标签行（最简洁）
 */
function formatArbitrageNewC(signal) {
    const yesBar = generateProgressBar(signal.yesPrice * 100, 9);
    const noBar = generateProgressBar(signal.noPrice * 100, 9);

    const text = `
💰 *套利警报*

\`\`\`
项目      价格    占比
YES      ${signal.yesPrice.toFixed(2)}    ${yesBar}
NO       ${signal.noPrice.toFixed(2)}    ${noBar}
合计     ${signal.sum.toFixed(2)}    ⚠️ 套利！
\`\`\`
    `.trim();

    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [[{ text: '📊 打开市场', url: marketUrl }]]
    };

    return { text, keyboard };
}

/**
 * 新方案D - 显示价格范围
 */
function formatArbitrageNewD(signal) {
    const yesBar = generateProgressBar(signal.yesPrice * 100, 9);
    const noBar = generateProgressBar(signal.noPrice * 100, 9);

    const text = `
💰 *套利警报*

📊 YES ${signal.yesPrice.toFixed(2)} · NO ${signal.noPrice.toFixed(2)}
\`\`\`
项目      价格    占比
YES      ${signal.yesPrice.toFixed(2)}    ${yesBar}
NO       ${signal.noPrice.toFixed(2)}    ${noBar}
合计     ${signal.sum.toFixed(2)}    ⚠️ 套利！
\`\`\`
    `.trim();

    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [[{ text: '📊 打开市场', url: marketUrl }]]
    };

    return { text, keyboard };
}

/**
 * 新方案E - 显示净利润（强调）
 */
function formatArbitrageNewE(signal) {
    const yesBar = generateProgressBar(signal.yesPrice * 100, 9);
    const noBar = generateProgressBar(signal.noPrice * 100, 9);

    const text = `
💰 *套利警报*

💎 净利 *${signal.netProfitPercent}%*
\`\`\`
项目      价格    占比
YES      ${signal.yesPrice.toFixed(2)}    ${yesBar}
NO       ${signal.noPrice.toFixed(2)}    ${noBar}
合计     ${signal.sum.toFixed(2)}    ⚠️ 套利！
\`\`\`
    `.trim();

    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [[{ text: '📊 打开市场', url: marketUrl }]]
    };

    return { text, keyboard };
}

/**
 * 获取当前时间 (HH:MM:SS)
 */
function getCurrentTime() {
    const now = new Date();
    return now.toLocaleTimeString('zh-CN', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

/**
 * 最终确定的格式 - 使用 --- 📊 --- 分隔线
 */
function formatArbitrageFinal(signal, options = {}) {
    const lang = options.lang || 'zh-CN';
    const i18n = t(lang);
    
    const yesBar = generateProgressBar(signal.yesPrice * 100, 9);
    const noBar = generateProgressBar(signal.noPrice * 100, 9);
    const time = getCurrentTime();

    // 计算差价百分比
    const gapPercent = ((1.00 - signal.sum) * 100).toFixed(2);

    const codeLines = lang === 'en' ? [
        'Side      Price    Ratio',
        `YES      ${signal.yesPrice.toFixed(3)}    ${yesBar}`,
        `NO       ${signal.noPrice.toFixed(3)}    ${noBar}`,
        '--- 📊 ---',
        `Total    ${signal.sum.toFixed(3)}`,
        `Gap      ${gapPercent}%`
    ] : [
        '方向      价格     占比',
        `YES      ${signal.yesPrice.toFixed(3)}    ${yesBar}`,
        `NO       ${signal.noPrice.toFixed(3)}    ${noBar}`,
        '--- 📊 ---',
        `合计     ${signal.sum.toFixed(3)}`,
        `差价     ${gapPercent}%`
    ];

    const codeBlock = ['```', ...codeLines, '```'].join('\n');

    const text = [
        `🏷️ ${signal.marketName || i18n.unknownMarket}`,
        codeBlock,
        `⏱️ ${time} ${i18n.arbitrage.title}`
    ].join('\n');

    const marketUrl = buildMarketUrl(signal);

    const keyboard = {
        inline_keyboard: [[{ text: i18n.openMarket, url: marketUrl }]]
    };

    return { text, keyboard };
}

/**
 * 默认格式化函数（支持多种变体）
 */
function formatArbitrageSignal(signal, variant = 'final', options = {}) {
    // 只有 final 变体支持多语言
    if (variant === 'final') {
        return formatArbitrageFinal(signal, options);
    }
    
    const formatters = {
        'v1': formatArbitrageV1,
        'v2': formatArbitrageV2,
        'v4': formatArbitrageV4,
        'v5': formatArbitrageV5,
        'newA': formatArbitrageNewA,
        'newB': formatArbitrageNewB,
        'newC': formatArbitrageNewC,
        'newD': formatArbitrageNewD,
        'newE': formatArbitrageNewE,
        'final': formatArbitrageFinal
    };

    const formatter = formatters[variant] || formatArbitrageFinal;
    return formatter(signal);
}

module.exports = {
    formatArbitrageSignal,
    formatArbitrageV1,
    formatArbitrageV2,
    formatArbitrageV4,
    formatArbitrageV5,
    formatArbitrageNewA,
    formatArbitrageNewB,
    formatArbitrageNewC,
    formatArbitrageNewD,
    formatArbitrageNewE,
    formatArbitrageFinal,
    generateProgressBar,
    getCurrentTime
};
