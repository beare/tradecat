/**
 * 订单簿失衡信号格式化
 */

const KALSHI_WEB_BASE = (process.env.KALSHI_WEB_BASE || '').replace(/\/$/, '');

function buildMarketUrl(ticker) {
  const marketPath = `/markets/${ticker}`;
  return KALSHI_WEB_BASE ? `${KALSHI_WEB_BASE}${marketPath}` : marketPath;
}

function format(signal, market, translate = s => s) {
  const title = translate(market?.title || signal.ticker);
  const { yesDepth, noDepth, imbalance, direction } = signal;
  
  const emoji = direction === 'YES' ? '🟢' : '🔴';
  const arrow = direction === 'YES' ? '⬆️' : '⬇️';
  
  return `📚 *订单簿失衡*

${emoji} *${title}*

${arrow} 方向: *${direction}* 侧深度更大
📊 失衡比: *${imbalance.toFixed(2)}x*

💰 YES 深度: $${yesDepth.toFixed(0)}
💰 NO 深度: $${noDepth.toFixed(0)}

⚠️ 深度失衡可能预示价格变动

🔗 [查看市场](${buildMarketUrl(signal.ticker)})`;
}

module.exports = { format };
