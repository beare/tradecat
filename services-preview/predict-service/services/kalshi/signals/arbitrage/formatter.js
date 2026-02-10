/**
 * 套利信号格式化
 */

const KALSHI_WEB_BASE = (process.env.KALSHI_WEB_BASE || '').replace(/\/$/, '');

function buildMarketUrl(ticker) {
  const marketPath = `/markets/${ticker}`;
  return KALSHI_WEB_BASE ? `${KALSHI_WEB_BASE}${marketPath}` : marketPath;
}

function format(signal, market, translate = s => s) {
  const title = translate(market?.title || signal.ticker);
  const { subType, netProfit, grossProfit } = signal;
  
  const profitPercent = (netProfit * 100).toFixed(2);
  const grossPercent = (grossProfit * 100).toFixed(2);
  
  if (subType === 'buy') {
    // 买入套利
    return `💰 *套利机会 (买入)*

📌 *${title}*

🎯 策略: 同时买入 YES + NO
💵 YES Ask: $${signal.yesAsk.toFixed(2)}
💵 NO Ask: $${signal.noAsk.toFixed(2)}
📊 总成本: $${signal.totalCost.toFixed(2)}

✅ 毛利润: ${grossPercent}%
✅ 净利润: *${profitPercent}%* (扣费后)

⚠️ 无论结果如何，保证获利

🔗 [查看市场](${buildMarketUrl(signal.ticker)})`;
  } else {
    // 卖出套利
    return `💰 *套利机会 (卖出)*

📌 *${title}*

🎯 策略: 同时卖出 YES + NO
💵 YES Bid: $${signal.yesBid.toFixed(2)}
💵 NO Bid: $${signal.noBid.toFixed(2)}
📊 总收入: $${signal.totalRevenue.toFixed(2)}

✅ 毛利润: ${grossPercent}%
✅ 净利润: *${profitPercent}%* (扣费后)

⚠️ 需要持有双边仓位

🔗 [查看市场](${buildMarketUrl(signal.ticker)})`;
  }
}

module.exports = { format };
