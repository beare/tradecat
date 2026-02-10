#!/usr/bin/env node
/**
 * 每日市场摘要生成器
 * 结合日志统计 + Gamma API 数据生成 AI 可用的摘要
 */

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { getFetchProxyOptions } = require('../utils/proxyAgent');

const LOG_PATH = process.env.LOG_PATH || path.join(__dirname, '../logs/pm2-out.log');
const GAMMA_API = (process.env.POLYMARKET_GAMMA_API_BASE || process.env.GAMMA_API_BASE || 'https://gamma-api.polymarket.com')
    .replace(/\/$/, '');

// 信号模式
const PATTERNS = {
    arbitrage: /🎉 发现套利/,
    orderbook: /🎉 发现订单簿/,
    smartMoneyOpen: /聪明钱建仓/,
    smartMoneyAdd: /聪明钱加仓/,
    smartMoneyClose: /聪明钱清仓/,
    largeTrade: /大额交易/,
    newMarket: /新市场/,
    signalSent: /发送信号:/
};

const DATE_PATTERN = /^(\d{4}-\d{2}-\d{2})T(\d{2}):/;

// 解析日志
async function parseLog(logPath, targetDate) {
    const stats = { byType: {}, byHour: {}, total: { detected: 0, sent: 0 } };
    Object.keys(PATTERNS).forEach(k => {
        stats.byType[k] = 0;
        stats.byHour[k] = Array(24).fill(0);
    });

    if (!fs.existsSync(logPath)) {
        console.warn(`日志文件不存在: ${logPath}`);
        return stats;
    }

    const rl = readline.createInterface({
        input: fs.createReadStream(logPath),
        crlfDelay: Infinity
    });

    for await (const line of rl) {
        const m = line.match(DATE_PATTERN);
        if (!m || (targetDate && m[1] !== targetDate)) continue;
        const h = parseInt(m[2], 10);

        for (const [type, pattern] of Object.entries(PATTERNS)) {
            if (pattern.test(line)) {
                stats.byType[type]++;
                stats.byHour[type][h]++;
                if (type === 'signalSent') stats.total.sent++;
                else stats.total.detected++;
            }
        }
    }
    return stats;
}

// 获取市场数据
async function fetchMarketData() {
    try {
        const fetch = (await import('node-fetch')).default;
        const fetchOptions = getFetchProxyOptions();
        const res = await fetch(
            `${GAMMA_API}/markets?active=true&closed=false&limit=200&order=volume24hr&ascending=false`,
            fetchOptions
        );
        return res.ok ? await res.json() : [];
    } catch (e) {
        console.warn('获取市场数据失败:', e.message);
        return [];
    }
}

// 生成摘要
async function generateSummary(targetDate) {
    console.log(`生成 ${targetDate} 每日摘要...\n`);

    // 1. 日志统计
    const logStats = await parseLog(LOG_PATH, targetDate);

    // 2. 市场数据
    const markets = await fetchMarketData();
    const totalVol24 = markets.reduce((s, m) => s + (parseFloat(m.volume24hr) || 0), 0);
    const totalLiq = markets.reduce((s, m) => s + (parseFloat(m.liquidityNum || m.liquidity) || 0), 0);

    // Top 5 热门
    const top5 = markets.slice(0, 5).map(m => ({
        question: m.question?.slice(0, 50),
        vol24: parseFloat(m.volume24hr) || 0,
        price: parseFloat(JSON.parse(m.outcomePrices || '["0.5"]')[0]) * 100
    }));

    // 价格异动 Top 5
    const priceChanges = markets
        .filter(m => m.oneDayPriceChange)
        .sort((a, b) => Math.abs(b.oneDayPriceChange) - Math.abs(a.oneDayPriceChange))
        .slice(0, 5)
        .map(m => ({
            question: m.question?.slice(0, 40),
            change: (parseFloat(m.oneDayPriceChange) * 100).toFixed(1)
        }));

    // 格式化输出
    const output = `
📅 Polymarket 每日简报 (${targetDate})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 市场概览
• 活跃市场: ${markets.length}+ 个
• 24h 总成交量: $${(totalVol24 / 1e6).toFixed(1)}M
• 总流动性: $${(totalLiq / 1e6).toFixed(1)}M

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔥 热门市场 Top 5
${top5.map((m, i) => `${i + 1}. ${m.question}... - $${(m.vol24 / 1e3).toFixed(0)}K (${m.price.toFixed(0)}%)`).join('\n')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 价格异动 Top 5
${priceChanges.map((m, i) => `${i + 1}. ${m.question}... ${m.change > 0 ? '+' : ''}${m.change}%`).join('\n')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🤖 信号统计
• 套利机会: ${logStats.byType.arbitrage || 0} 次
• 订单簿失衡: ${logStats.byType.orderbook || 0} 次
• 聪明钱动向: ${(logStats.byType.smartMoneyOpen || 0) + (logStats.byType.smartMoneyAdd || 0) + (logStats.byType.smartMoneyClose || 0)} 次
  - 建仓: ${logStats.byType.smartMoneyOpen || 0}
  - 加仓: ${logStats.byType.smartMoneyAdd || 0}
  - 清仓: ${logStats.byType.smartMoneyClose || 0}
• 大额交易: ${logStats.byType.largeTrade || 0} 次
• 新市场: ${logStats.byType.newMarket || 0} 个

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📤 推送统计
• 检测信号: ${logStats.total.detected} 条
• 实际推送: ${logStats.total.sent} 条
• 推送率: ${logStats.total.detected > 0 ? (logStats.total.sent / logStats.total.detected * 100).toFixed(1) : 0}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⏰ 套利信号时段分布
${formatHourlyChart(logStats.byHour.arbitrage || Array(24).fill(0))}
`.trim();

    return output;
}

function formatHourlyChart(hourly) {
    const max = Math.max(...hourly, 1);
    const lines = [];
    for (let h = 0; h < 24; h += 4) {
        const counts = hourly.slice(h, h + 4);
        const bars = counts.map(c => {
            const len = Math.round(c / max * 8);
            return '█'.repeat(len).padEnd(8, '░');
        });
        lines.push(`${String(h).padStart(2, '0')}-${String(h + 3).padStart(2, '0')}h: ${bars.join(' ')} (${counts.join('/')})`);
    }
    return lines.join('\n');
}

// 主函数
async function main() {
    const targetDate = process.argv[2] || new Date().toISOString().slice(0, 10);
    const summary = await generateSummary(targetDate);
    console.log(summary);

    // 保存到文件
    const outPath = path.join(__dirname, `../data/daily-summary-${targetDate}.txt`);
    fs.writeFileSync(outPath, summary);
    console.log(`\n✅ 已保存到: ${outPath}`);
}

main().catch(console.error);
