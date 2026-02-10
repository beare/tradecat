/**
 * 价格突变信号格式化
 */

const KALSHI_WEB_BASE = (process.env.KALSHI_WEB_BASE || '').replace(/\/$/, '');

function buildMarketUrl(ticker) {
  const marketPath = `/markets/${ticker}`;
  return KALSHI_WEB_BASE ? `${KALSHI_WEB_BASE}${marketPath}` : marketPath;
}

function format(signal, translate = s => s) {
  const { market, oldPrice, newPrice, change, direction } = signal;
  const title = translate(market?.title || signal.ticker);
  
  const emoji = direction === 'up' ? '📈' : '📉';
  const arrow = direction === 'up' ? '⬆️' : '⬇️';
  const changePercent = (change * 100).toFixed(1);
  
  return `${emoji} *价格突变*

📌 *${title}*

${arrow} 变化: *${direction === 'up' ? '+' : '-'}${changePercent}%*
💰 $${oldPrice.toFixed(2)} → $${newPrice.toFixed(2)}

⚠️ 短时间内价格剧烈波动

🔗 [查看市场](${buildMarketUrl(signal.ticker)})`;
}

module.exports = { format };
