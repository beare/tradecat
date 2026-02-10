#!/usr/bin/env node
/**
 * CSV 报告生成器 - 带真实市场链接
 * 包含：Top 15 市场 + 活跃时段 + 买卖比例 + 市场类别
 */

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { HttpsProxyAgent } = require('https-proxy-agent');
const { SocksProxyAgent } = require('socks-proxy-agent');
const fetch = require('node-fetch');
const GoogleTranslateProxy = require('../translation/google-proxy');

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

const GAMMA_API = (process.env.POLYMARKET_GAMMA_API_BASE || process.env.GAMMA_API_BASE || process.env.NEW_MARKET_GAMMA_API || '')
  .replace(/\/$/, '');
const POLYMARKET_WEB_BASE = (process.env.POLYMARKET_WEB_BASE || '').replace(/\/$/, '');
const TRANSLATE_ENABLED = process.env.CSV_TRANSLATE !== 'false';
const TRANSLATE_MAX = Number(process.env.CSV_TRANSLATE_MAX || 120);
const TRANSLATE_CACHE_FILE = process.env.CSV_TRANSLATE_CACHE_FILE || path.join(__dirname, '../data/translation-cache.json');
const DEFAULT_LOG_FILE = process.env.CSV_LOG_FILE || path.join(__dirname, '../logs/polymarket.log');
const LOG_FILE = process.argv[2] || DEFAULT_LOG_FILE;

const getProxyUrl = () =>
  process.env.HTTPS_PROXY
  || process.env.HTTP_PROXY
  || process.env.https_proxy
  || process.env.http_proxy
  || process.env.PROXY;

const createProxyAgent = () => {
  const proxyUrl = getProxyUrl();
  if (!proxyUrl) return null;
  if (proxyUrl.startsWith('socks')) return new SocksProxyAgent(proxyUrl);
  return new HttpsProxyAgent(proxyUrl);
};

const proxyAgent = createProxyAgent();
const DEFAULT_FETCH_TIMEOUT_MS = Number(process.env.CSV_FETCH_TIMEOUT_MS || 15000);
const fetchJson = async (url, timeoutMs = DEFAULT_FETCH_TIMEOUT_MS) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, proxyAgent ? { agent: proxyAgent, signal: controller.signal } : { signal: controller.signal });
    return await res.json();
  } catch (error) {
    console.error(`⚠️ API 请求失败: ${url} (${error?.message || error})`);
    return null;
  } finally {
    clearTimeout(timer);
  }
};

const csvEscape = (value) => {
  if (value == null) return '';
  const s = String(value);
  if (s.includes('"') || s.includes(',') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return `"${s}"`;
};

const parseOutcomePrice = (raw) => {
  if (!raw) return '';
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? (arr[0] ?? '') : '';
  } catch {
    return '';
  }
};

// 滚动24小时：计算24小时前的时间戳
const now = new Date();
const hours24Ago = new Date(now.getTime() - 24 * 60 * 60 * 1000);

const ANSI_REGEX = /\x1b\[[0-9;]*m/g;
const stripAnsi = (line) => line.replace(ANSI_REGEX, '');
const pad2 = (num) => String(num).padStart(2, '0');
const formatLocalMinute = (date) =>
  `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
const formatLocalDateTime = (date) =>
  `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;

const parseLineTime = (line, state) => {
  // ISO 或空格分隔: 2026-01-18T00:25:22 / 2026-01-18 00:25:22
  const fullMatch = line.match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
  if (fullMatch) {
    const dt = new Date(`${fullMatch[1]}T${fullMatch[2]}`);
    if (!Number.isNaN(dt.getTime())) {
      const [h, m, s] = fullMatch[2].split(':').map(Number);
      state.currentDate = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
      state.lastTimeSec = h * 3600 + m * 60 + s;
      return dt;
    }
  }

  // 仅时间: 00:25:22
  const timeOnlyMatch = line.match(/(\d{2}:\d{2}:\d{2})/);
  if (timeOnlyMatch) {
    const [h, m, s] = timeOnlyMatch[1].split(':').map(Number);
    if (!state.currentDate) {
      state.currentDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    }
    const timeSec = h * 3600 + m * 60 + s;
    if (state.lastTimeSec !== null && timeSec + 6 * 3600 < state.lastTimeSec) {
      state.currentDate = new Date(state.currentDate.getTime() + 24 * 60 * 60 * 1000);
    }
    state.lastTimeSec = timeSec;
    const dt = new Date(state.currentDate.getTime());
    dt.setHours(h, m, s, 0);
    return dt;
  }

  return null;
};

const marketSlugs = new Map();
const ENABLE_API_RANKINGS = process.env.CSV_ENABLE_API_RANKINGS === 'true';
let translationCache = null;
let translationCacheDirty = false;

const normalizeKey = (text) => text.trim().toLowerCase();
const hasChinese = (text) => /[\u4e00-\u9fff]/.test(text);
const simplifyName = (text) => text.replace(/\d{4}-\d{2}-\d{2}/g, '').replace(/\s+/g, ' ').trim();

const loadTranslationCache = () => {
  if (translationCache) return translationCache;
  translationCache = new Map();
  try {
    if (fs.existsSync(TRANSLATE_CACHE_FILE)) {
      const raw = fs.readFileSync(TRANSLATE_CACHE_FILE, 'utf-8');
      const data = JSON.parse(raw);
      Object.entries(data).forEach(([key, value]) => {
        translationCache.set(key, value);
      });
    }
  } catch (e) {
    console.error(`⚠️ 翻译缓存加载失败: ${e?.message || e}`);
  }
  return translationCache;
};

const saveTranslationCache = () => {
  if (!translationCacheDirty || !translationCache) return;
  try {
    const obj = {};
    translationCache.forEach((value, key) => {
      obj[key] = value;
    });
    fs.writeFileSync(TRANSLATE_CACHE_FILE, JSON.stringify(obj, null, 2), 'utf-8');
    translationCacheDirty = false;
  } catch (e) {
    console.error(`⚠️ 翻译缓存写入失败: ${e?.message || e}`);
  }
};

const getCachedTranslation = (name) => {
  if (!name) return null;
  const cache = loadTranslationCache();
  const key = normalizeKey(name);
  if (cache.has(key)) return cache.get(key);
  const simplified = simplifyName(name);
  if (simplified && simplified !== name) {
    const skey = normalizeKey(simplified);
    if (cache.has(skey)) return cache.get(skey);
  }
  return null;
};

const setCachedTranslation = (name, translation) => {
  if (!name || !translation) return;
  const cache = loadTranslationCache();
  const key = normalizeKey(name);
  if (!cache.has(key)) {
    cache.set(key, translation);
    translationCacheDirty = true;
  }
};

const translateNames = async (names) => {
  const result = new Map();
  const unique = Array.from(new Set(names.filter(Boolean)));
  const pending = [];

  unique.forEach((name) => {
    if (!name) return;
    if (hasChinese(name)) {
      result.set(name, name);
      return;
    }
    const cached = getCachedTranslation(name);
    if (cached) {
      result.set(name, cached);
      return;
    }
    pending.push(name);
  });

  if (!TRANSLATE_ENABLED || pending.length === 0) {
    return result;
  }

  const translator = new GoogleTranslateProxy(getProxyUrl());
  const limit = Math.min(pending.length, Math.max(0, TRANSLATE_MAX));
  for (let i = 0; i < limit; i++) {
    const name = pending[i];
    try {
      const translated = await translator.translate(name, 'en', 'zh-CN');
      if (translated && translated !== name) {
        result.set(name, translated);
        setCachedTranslation(name, translated);
      } else {
        result.set(name, name);
      }
    } catch (e) {
      result.set(name, name);
    }
  }

  // 超出翻译上限的直接回退为原文
  for (let i = limit; i < pending.length; i++) {
    result.set(pending[i], pending[i]);
  }

  saveTranslationCache();
  return result;
};

// 市场类别关键词
const CATEGORY_KEYWORDS = {
  sports: ['FC', 'vs.', 'NBA', 'NFL', 'NHL', 'MLB', 'UFC', 'win on', 'Super Bowl', 'World Cup', 'Finals', 'Lakers', 'Celtics', 'Arsenal', 'Liverpool', 'Manchester', 'Chelsea', 'Pistons', 'Mavericks', 'Warriors', 'Rams', 'Falcons', 'Bulls', 'Heat', 'Knicks', 'Spread:', 'O/U', 'Grizzlies', 'Nuggets', 'Pacers'],
  crypto: ['Bitcoin', 'Ethereum', 'BTC', 'ETH', 'Solana', 'XRP', 'crypto', 'token', 'airdrop', 'FDV', 'market cap', 'Lighter', 'Satoshi', 'dip to'],
  politics: ['Trump', 'Biden', 'election', 'President', 'Congress', 'Senate', 'Governor', 'Maduro', 'Newsom', 'Republican', 'Democrat', 'nomination'],
  entertainment: ['movie', 'film', 'Oscar', 'Grammy', 'Netflix', 'Disney', 'Stranger Things', 'Avatar', 'tweets', 'Elon Musk'],
  finance: ['Fed', 'interest rate', 'S&P', 'Nasdaq', 'Gold', 'Silver', 'stock', 'TSLA', 'AAPL', 'GOOGL', 'MSFT', 'close between']
};

function categorizeMarket(name) {
  const lower = name.toLowerCase();
  for (const [cat, keywords] of Object.entries(CATEGORY_KEYWORDS)) {
    if (keywords.some(k => lower.includes(k.toLowerCase()))) return cat;
  }
  return 'other';
}

function rememberSlug(name, slug) {
  if (!name || !slug) return;
  marketSlugs.set(name, slug);
  marketSlugs.set(name.toLowerCase(), slug);
  const simplified = name.replace(/\d{4}-\d{2}-\d{2}/g, '').trim();
  if (simplified && simplified !== name) {
    marketSlugs.set(simplified, slug);
    marketSlugs.set(simplified.toLowerCase(), slug);
  }
}

function findSlug(marketName) {
  if (!marketName) return null;
  
  // 精确匹配
  if (marketSlugs.has(marketName)) return marketSlugs.get(marketName);
  
  const lower = marketName.toLowerCase();
  if (marketSlugs.has(lower)) return marketSlugs.get(lower);
  
  // 去掉问号和空格
  const normalized = marketName.replace(/\?$/, '').trim();
  if (marketSlugs.has(normalized)) return marketSlugs.get(normalized);
  if (marketSlugs.has(normalized.toLowerCase())) return marketSlugs.get(normalized.toLowerCase());
  
  // 模糊匹配：遍历所有市场找最佳匹配
  let bestMatch = null;
  let bestScore = 0;
  
  for (const [question, slug] of marketSlugs) {
    if (typeof question !== 'string') continue;
    
    const qLower = question.toLowerCase();
    
    // 完全包含
    if (qLower.includes(lower) || lower.includes(qLower)) {
      const score = Math.min(question.length, marketName.length) / Math.max(question.length, marketName.length);
      if (score > bestScore) {
        bestScore = score;
        bestMatch = slug;
      }
    }
    
    // 关键词匹配
    const keywords = lower.split(/\s+/).filter(w => w.length > 3);
    const matchCount = keywords.filter(k => qLower.includes(k)).length;
    if (matchCount >= 3 && matchCount / keywords.length > bestScore) {
      bestScore = matchCount / keywords.length;
      bestMatch = slug;
    }
  }
  
  return bestScore > 0.5 ? bestMatch : null;
}

async function extractData() {
  const arbCounts = new Map(), arbProfits = new Map();
  const largeTradeCounts = new Map(), orderbookCounts = new Map(), smartMoneyCounts = new Map();
  const newMarketCounts = new Map();
  
  // 统计
  const hourlySignals = new Array(24).fill(0);
  const hourlyByType = { arb: new Array(24).fill(0), large: new Array(24).fill(0), orderbook: new Array(24).fill(0), smart: new Array(24).fill(0) };
  const buySellStats = { buy: 0, sell: 0 };
  const smartMoneyOps = { open: 0, add: 0, close: 0 };
  const profitRanges = { '0-2%': 0, '2-5%': 0, '5-10%': 0, '10%+': 0 };
  const categoryStats = { sports: 0, crypto: 0, politics: 0, entertainment: 0, finance: 0, other: 0 };
  
  // 新增统计
  const smartMoneyByCategory = { sports: 0, crypto: 0, politics: 0, entertainment: 0, finance: 0, other: 0 };
  const signalBursts = [];
  const marketSignalTypes = new Map();
  const seenSignals = new Set();
  let lastBurstCheck = null;
  let burstCount = 0;
  
  let lastMarketName = null;
  let lastMarketTime = null;
  const timeState = { currentDate: null, lastTimeSec: null };
  const getSignalType = (s) => {
    if (s.includes('大额交易')) return 'large';
    if (s.includes('订单簿')) return 'orderbook';
    if (s.includes('聪明钱')) return 'smart';
    if (s.includes('新市场')) return 'newMarket';
    if (s.includes('套利')) return 'arb';
    return null;
  };

  if (!fs.existsSync(LOG_FILE)) {
    console.error(`⚠️ 日志文件不存在: ${LOG_FILE}`);
    return { 
      arbCounts, arbProfits, largeTradeCounts, orderbookCounts, smartMoneyCounts, newMarketCounts,
      hourlySignals, hourlyByType, buySellStats, smartMoneyOps, profitRanges, categoryStats,
      smartMoneyByCategory, signalBursts, marketSignalTypes
    };
  }
  
  const rl = readline.createInterface({
    input: fs.createReadStream(LOG_FILE),
    crlfDelay: Infinity
  });
  
  for await (const rawLine of rl) {
    const line = stripAnsi(rawLine);
    const lineTime = parseLineTime(line, timeState);
    
    // 从日志中提取市场链接（尽量本地化）
    const urlMatch = line.match(/https?:\/\/polymarket\.com\/event\/([a-z0-9-]+)/i);
    if (urlMatch) {
      const slug = urlMatch[1];
      const nameMatch = line.match(/市场[:：]\s*([^,，\n]+)/);
      const name = nameMatch ? nameMatch[1].trim() : lastMarketName;
      if (name) rememberSlug(name, slug);
    }
    
    // 🏷️ 标签行（可能无时间戳）
    const tagMatch = line.match(/🏷️\s*(.+)$/);
    if (tagMatch) {
      lastMarketName = tagMatch[1].trim();
      if (lineTime) {
        lastMarketTime = lineTime;
      }
      categoryStats[categorizeMarket(lastMarketName)]++;
      continue;
    }
    
    if (!lineTime || lineTime < hours24Ago || lineTime > now) {
      continue;
    }
    
    // 提取时间戳用于爆发检测
    if (line.includes('⏱')) {
      const tsKey = formatLocalMinute(lineTime);
      if (lastBurstCheck === tsKey) {
        burstCount++;
      } else {
        if (burstCount >= 20) {
          signalBursts.push({ time: lastBurstCheck, count: burstCount });
        }
        lastBurstCheck = tsKey;
        burstCount = 1;
      }
    }
    
    // 提取小时
    const hour = lineTime.getHours();
    
    if (hour >= 0 && line.includes('⏱')) {
      hourlySignals[hour]++;
    }

    // 聪明钱操作类型统计
    if (line.includes('聪明钱')) {
      if (line.includes('建仓')) { smartMoneyOps.open++; buySellStats.buy++; }
      else if (line.includes('加仓')) { smartMoneyOps.add++; buySellStats.buy++; }
      else if (line.includes('清仓')) { smartMoneyOps.close++; buySellStats.sell++; }
    }
    
    // 套利
    const arbMatch = line.match(/🎉 发现套利.*?市场:\s*(.+?),\s*净利润:\s*([\d.]+)%/);
    if (arbMatch) {
      const name = arbMatch[1].trim();
      const profit = parseFloat(arbMatch[2]);
      arbCounts.set(name, (arbCounts.get(name) || 0) + 1);
      arbProfits.set(name, Math.max(arbProfits.get(name) || 0, profit));
      
      const cat = categorizeMarket(name);
      categoryStats[cat]++;
      
      if (!marketSignalTypes.has(name)) marketSignalTypes.set(name, new Set());
      marketSignalTypes.get(name).add('arb');
      
      if (profit < 2) profitRanges['0-2%']++;
      else if (profit < 5) profitRanges['2-5%']++;
      else if (profit < 10) profitRanges['5-10%']++;
      else profitRanges['10%+']++;
      
      if (hour >= 0) hourlyByType.arb[hour]++;
      continue;
    }
    
    // 新市场
    if (line.includes('🆕 新市场')) {
      if (hour >= 0) hourlySignals[hour]++;
    }
    
    // ⏱️ 信号行统计（依赖前置 🏷️ 市场名）
    if (line.includes('⏱')) {
      const signalType = getSignalType(line);
      if (signalType && lastMarketName) {
        const name = lastMarketName;
        const dedupKey = `${signalType}:${name}:${lineTime.getTime()}`;
        if (!seenSignals.has(dedupKey)) {
          seenSignals.add(dedupKey);
          
          if (!marketSignalTypes.has(name)) marketSignalTypes.set(name, new Set());
          
          if (signalType === 'large') {
            largeTradeCounts.set(name, (largeTradeCounts.get(name) || 0) + 1);
            marketSignalTypes.get(name).add('large');
            if (hour >= 0) hourlyByType.large[hour]++;
          } else if (signalType === 'orderbook') {
            orderbookCounts.set(name, (orderbookCounts.get(name) || 0) + 1);
            marketSignalTypes.get(name).add('orderbook');
            if (hour >= 0) hourlyByType.orderbook[hour]++;
          } else if (signalType === 'smart') {
            smartMoneyCounts.set(name, (smartMoneyCounts.get(name) || 0) + 1);
            marketSignalTypes.get(name).add('smart');
            smartMoneyByCategory[categorizeMarket(name)]++;
            if (hour >= 0) hourlyByType.smart[hour]++;
          } else if (signalType === 'newMarket') {
            newMarketCounts.set(name, (newMarketCounts.get(name) || 0) + 1);
          } else if (signalType === 'arb') {
            arbCounts.set(name, (arbCounts.get(name) || 0) + 1);
            marketSignalTypes.get(name).add('arb');
            if (hour >= 0) hourlyByType.arb[hour]++;
          }
        }
      }
      if (lineTime) {
        lastMarketTime = lineTime;
      }
    }
    
    // MessageUpdater
    const msgMatch = line.match(/(\d{2}:\d{2}:\d{2}).*?\[MessageUpdater\].*?\((\w+)\)/);
    if (msgMatch && lastMarketName && lastMarketTime) {
      const type = msgMatch[2];
      if (Math.abs(lineTime.getTime() - lastMarketTime.getTime()) / 1000 <= 2) {
        const name = lastMarketName;
        const typeMap = {
          largeTrade: 'large',
          orderbook: 'orderbook',
          smartMoney: 'smart',
          newMarket: 'newMarket'
        };
        const mappedType = typeMap[type] || null;
        const dedupKey = mappedType ? `${mappedType}:${name}:${lineTime.getTime()}` : null;
        if (dedupKey && seenSignals.has(dedupKey)) {
          continue;
        }
        if (dedupKey) {
          seenSignals.add(dedupKey);
        }
        
        if (!marketSignalTypes.has(name)) marketSignalTypes.set(name, new Set());
        
        if (type === 'largeTrade') {
          largeTradeCounts.set(name, (largeTradeCounts.get(name) || 0) + 1);
          marketSignalTypes.get(name).add('large');
          if (hour >= 0) hourlyByType.large[hour]++;
        }
        else if (type === 'orderbook') {
          orderbookCounts.set(name, (orderbookCounts.get(name) || 0) + 1);
          marketSignalTypes.get(name).add('orderbook');
          if (hour >= 0) hourlyByType.orderbook[hour]++;
        }
        else if (type === 'smartMoney') {
          smartMoneyCounts.set(name, (smartMoneyCounts.get(name) || 0) + 1);
          marketSignalTypes.get(name).add('smart');
          smartMoneyByCategory[categorizeMarket(name)]++;
        }
        else if (type === 'newMarket') {
          newMarketCounts.set(name, (newMarketCounts.get(name) || 0) + 1);
        }
      }
    }
  }
  
  if (burstCount >= 20) {
    signalBursts.push({ time: lastBurstCheck, count: burstCount });
  }
  
  return { 
    arbCounts, arbProfits, largeTradeCounts, orderbookCounts, smartMoneyCounts, newMarketCounts,
    hourlySignals, hourlyByType, buySellStats, smartMoneyOps, profitRanges, categoryStats,
    smartMoneyByCategory, signalBursts, marketSignalTypes
  };
}

async function main() {
  const timeRange = `${formatLocalDateTime(hours24Ago)} ~ ${formatLocalDateTime(now)}`;
  console.error(`📊 生成 CSV 报告 (滚动24小时: ${timeRange})...\n`);
  
  const data = await extractData();
  
  const sortTop = (m, n = 15) => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
  const link = n => {
    const s = findSlug(n);
    return s && POLYMARKET_WEB_BASE ? `${POLYMARKET_WEB_BASE}/event/${s}` : '';
  };
  
  const arbTop = sortTop(data.arbCounts);
  const largeTop = sortTop(data.largeTradeCounts);
  const obTop = sortTop(data.orderbookCounts);
  const smartTop = sortTop(data.smartMoneyCounts);
  const newMarketTop = sortTop(data.newMarketCounts);
  
  // 综合热门市场
  const combined = new Map();
  [data.arbCounts, data.largeTradeCounts, data.orderbookCounts, data.smartMoneyCounts].forEach(m => {
    for (const [k, v] of m) combined.set(k, (combined.get(k) || 0) + v);
  });
  const combinedTop = sortTop(combined);
  
  const nameSet = new Set();
  [arbTop, largeTop, obTop, smartTop, newMarketTop, combinedTop].forEach((list) => {
    list.forEach(([n]) => nameSet.add(n));
  });
  const highFreqArb = [...data.arbCounts.entries()].filter(([, c]) => c >= 10).sort((a, b) => b[1] - a[1]);
  highFreqArb.forEach(([n]) => nameSet.add(n));
  const translatedNames = await translateNames(Array.from(nameSet));
  const displayName = (name) => translatedNames.get(name) || name;

  let csv = '';
  
  // 1. 套利信号
  csv += '# 套利信号 Top 15\n排名,市场名称,出现次数,最高利润%,链接\n';
  arbTop.forEach(([n, c], i) => csv += `${i+1},${csvEscape(displayName(n))},${c},${data.arbProfits.get(n)||''},${link(n)}\n`);
  
  // 2. 大额交易
  csv += '\n# 大额交易 Top 15\n排名,市场名称,交易次数,链接\n';
  largeTop.forEach(([n, c], i) => csv += `${i+1},${csvEscape(displayName(n))},${c},${link(n)}\n`);
  
  // 3. 订单簿失衡
  csv += '\n# 订单簿失衡 Top 15\n排名,市场名称,失衡次数,链接\n';
  obTop.forEach(([n, c], i) => csv += `${i+1},${csvEscape(displayName(n))},${c},${link(n)}\n`);
  
  // 4. 聪明钱
  csv += '\n# 聪明钱 Top 15\n排名,市场名称,信号次数,链接\n';
  smartTop.forEach(([n, c], i) => csv += `${i+1},${csvEscape(displayName(n))},${c},${link(n)}\n`);
  
  // 5. 新市场 Top 15
  csv += '\n# 新市场 Top 15\n排名,市场名称,出现次数,链接\n';
  newMarketTop.forEach(([n, c], i) => csv += `${i+1},${csvEscape(displayName(n))},${c},${link(n)}\n`);
  
  // 6. 综合热门市场 Top 15
  csv += '\n# 综合热门市场 Top 15\n排名,市场名称,套利,大额,订单簿,聪明钱,总计,链接\n';
  combinedTop.forEach(([n, total], i) => {
    const arb = data.arbCounts.get(n) || 0;
    const large = data.largeTradeCounts.get(n) || 0;
    const ob = data.orderbookCounts.get(n) || 0;
    const smart = data.smartMoneyCounts.get(n) || 0;
    csv += `${i+1},${csvEscape(displayName(n))},${arb},${large},${ob},${smart},${total},${link(n)}\n`;
  });
  
  // 7. 活跃时段分布 (本地时间)
  csv += '\n# 活跃时段分布 (本地时间)\n小时,信号数量,占比%\n';
  const totalSignals = data.hourlySignals.reduce((a, b) => a + b, 0);
  data.hourlySignals.forEach((count, hour) => {
    const pct = totalSignals > 0 ? (count / totalSignals * 100).toFixed(1) : 0;
    csv += `${hour.toString().padStart(2, '0')}:00,${count},${pct}\n`;
  });
  
  // 8. 时段-类型分布
  csv += '\n# 时段-类型分布 (本地时间)\n小时,套利,大额交易,订单簿,聪明钱\n';
  for (let h = 0; h < 24; h++) {
    csv += `${h.toString().padStart(2, '0')}:00,${data.hourlyByType.arb[h]},${data.hourlyByType.large[h]},${data.hourlyByType.orderbook[h]},${data.hourlyByType.smart[h]}\n`;
  }
  
  // 9. 买卖比例
  csv += '\n# 买卖比例\n类型,数量,占比%\n';
  const totalBuySell = data.buySellStats.buy + data.buySellStats.sell;
  if (totalBuySell > 0) {
    csv += `买入(建仓/加仓),${data.buySellStats.buy},${(data.buySellStats.buy / totalBuySell * 100).toFixed(1)}\n`;
    csv += `卖出(清仓),${data.buySellStats.sell},${(data.buySellStats.sell / totalBuySell * 100).toFixed(1)}\n`;
    csv += `买卖比,${(data.buySellStats.buy / (data.buySellStats.sell || 1)).toFixed(2)}:1,\n`;
  }
  
  // 10. 聪明钱操作类型
  csv += '\n# 聪明钱操作类型\n类型,数量,占比%\n';
  const totalOps = data.smartMoneyOps.open + data.smartMoneyOps.add + data.smartMoneyOps.close;
  if (totalOps > 0) {
    csv += `建仓,${data.smartMoneyOps.open},${(data.smartMoneyOps.open / totalOps * 100).toFixed(1)}\n`;
    csv += `加仓,${data.smartMoneyOps.add},${(data.smartMoneyOps.add / totalOps * 100).toFixed(1)}\n`;
    csv += `清仓,${data.smartMoneyOps.close},${(data.smartMoneyOps.close / totalOps * 100).toFixed(1)}\n`;
  }
  
  // 11. 套利利润分布
  csv += '\n# 套利利润分布\n利润区间,数量,占比%\n';
  const totalProfit = Object.values(data.profitRanges).reduce((a, b) => a + b, 0);
  if (totalProfit > 0) {
    Object.entries(data.profitRanges).forEach(([range, count]) => {
      csv += `${range},${count},${(count / totalProfit * 100).toFixed(1)}\n`;
    });
  }
  
  // 12. 市场类别分布
  csv += '\n# 市场类别分布\n类别,信号数量,占比%\n';
  const totalCat = Object.values(data.categoryStats).reduce((a, b) => a + b, 0);
  const catNames = { sports: '体育', crypto: '加密货币', politics: '政治', entertainment: '娱乐', finance: '金融', other: '其他' };
  Object.entries(data.categoryStats)
    .sort((a, b) => b[1] - a[1])
    .forEach(([cat, count]) => {
      const pct = totalCat > 0 ? (count / totalCat * 100).toFixed(1) : 0;
      csv += `${catNames[cat]},${count},${pct}\n`;
    });
  
  // 13. 高频套利市场 (10次以上)
  csv += '\n# 高频套利市场 (10次以上)\n排名,市场名称,出现次数,最高利润%,链接\n';
  highFreqArb.forEach(([n, c], i) => {
    csv += `${i+1},${csvEscape(displayName(n))},${c},${data.arbProfits.get(n)||''},${link(n)}\n`;
  });
  
  // 14. 聪明钱偏好类别
  csv += '\n# 聪明钱偏好类别\n类别,信号数量,占比%\n';
  const totalSmartCat = Object.values(data.smartMoneyByCategory).reduce((a, b) => a + b, 0);
  Object.entries(data.smartMoneyByCategory)
    .sort((a, b) => b[1] - a[1])
    .forEach(([cat, count]) => {
      const pct = totalSmartCat > 0 ? (count / totalSmartCat * 100).toFixed(1) : 0;
      csv += `${catNames[cat]},${count},${pct}\n`;
    });
  
  // 15. 信号频率趋势 (环比)
  csv += '\n# 信号频率趋势 (环比)\n小时,信号数,环比变化%\n';
  data.hourlySignals.forEach((count, hour) => {
    const prevHour = hour === 0 ? 23 : hour - 1;
    const prev = data.hourlySignals[prevHour];
    const change = prev > 0 ? ((count - prev) / prev * 100).toFixed(1) : 'N/A';
    csv += `${hour.toString().padStart(2, '0')}:00,${count},${change}\n`;
  });
  
  // 16. 市场重复出现率 (出现在多种信号类型)
  csv += '\n# 市场重复出现率 (跨信号类型)\n信号类型数,市场数量,占比%\n';
  const typeCountDist = { 1: 0, 2: 0, 3: 0, 4: 0 };
  for (const [, types] of data.marketSignalTypes) {
    typeCountDist[types.size] = (typeCountDist[types.size] || 0) + 1;
  }
  const totalMarkets = Object.values(typeCountDist).reduce((a, b) => a + b, 0);
  Object.entries(typeCountDist).forEach(([n, count]) => {
    const pct = totalMarkets > 0 ? (count / totalMarkets * 100).toFixed(1) : 0;
    csv += `${n}种,${count},${pct}\n`;
  });
  
  // 17. 信号密集时段 (5分钟内爆发)
  csv += '\n# 信号密集时段 (5分钟内20+信号)\n时间,信号数量\n';
  data.signalBursts.sort((a, b) => b.count - a.count).slice(0, 15).forEach(b => {
    csv += `${b.time},${b.count}\n`;
  });
  
  // ========== API 数据模块 ==========
  if (ENABLE_API_RANKINGS) {
    console.error('📥 获取市场排行数据...');

    const [byVolume, byLiquidity] = await Promise.all([
      fetchJson(`${GAMMA_API}/markets?limit=20&order=volume24hr&ascending=false&active=true`).catch(() => []),
      fetchJson(`${GAMMA_API}/markets?limit=20&order=liquidity&ascending=false&active=true`).catch(() => [])
    ]);

    const apiNames = []
      .concat(byVolume.map(m => m.question).filter(Boolean))
      .concat(byLiquidity.map(m => m.question).filter(Boolean));
    const apiTranslated = await translateNames(apiNames);
    apiTranslated.forEach((value, key) => translatedNames.set(key, value));

    const getLink = (m) => {
      const slug = m.events?.[0]?.slug || m.slug;
      if (m.question && slug) {
        rememberSlug(m.question, slug);
      }
      return slug && POLYMARKET_WEB_BASE ? `${POLYMARKET_WEB_BASE}/event/${slug}` : '';
    };

    // 18. 24h成交量 Top 15
    csv += '\n# 24h成交量 Top 15\n排名,市场名称,24h成交量,价格,链接\n';
    byVolume.slice(0, 15).forEach((m, i) => {
      const price = parseOutcomePrice(m.outcomePrices);
      csv += `${i+1},${csvEscape(displayName(m.question))},${Math.round(m.volume24hr || 0)},${price},${getLink(m)}\n`;
    });

    // 19. 流动性 Top 15
    csv += '\n# 流动性 Top 15\n排名,市场名称,流动性,24h成交量,链接\n';
    byLiquidity.slice(0, 15).forEach((m, i) => {
      csv += `${i+1},${csvEscape(displayName(m.question))},${Math.round(m.liquidity || 0)},${Math.round(m.volume24hr || 0)},${getLink(m)}\n`;
    });

    // 20. 24h涨幅 Top 15
    const withChange = byVolume.filter(m => m.oneDayPriceChange != null);
    const gainers = [...withChange].sort((a, b) => b.oneDayPriceChange - a.oneDayPriceChange);
    csv += '\n# 24h涨幅 Top 15\n排名,市场名称,涨幅%,当前价格,链接\n';
    gainers.slice(0, 15).forEach((m, i) => {
      const price = parseOutcomePrice(m.outcomePrices);
      csv += `${i+1},${csvEscape(displayName(m.question))},${(m.oneDayPriceChange * 100).toFixed(1)},${price},${getLink(m)}\n`;
    });

    // 21. 24h跌幅 Top 15
    const losers = [...withChange].sort((a, b) => a.oneDayPriceChange - b.oneDayPriceChange);
    csv += '\n# 24h跌幅 Top 15\n排名,市场名称,跌幅%,当前价格,链接\n';
    losers.slice(0, 15).forEach((m, i) => {
      const price = parseOutcomePrice(m.outcomePrices);
      csv += `${i+1},${csvEscape(displayName(m.question))},${(m.oneDayPriceChange * 100).toFixed(1)},${price},${getLink(m)}\n`;
    });
  } else {
    console.error('ℹ️ 已跳过 API 排行数据（CSV_ENABLE_API_RANKINGS 未启用）');
  }
  
  console.log(csv);
}

main().catch(console.error);
