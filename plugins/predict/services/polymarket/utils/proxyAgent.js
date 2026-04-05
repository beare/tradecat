/**
 * 代理配置模块
 * 为 Telegram Bot 和其他网络请求配置代理
 */

const { HttpsProxyAgent } = require('https-proxy-agent');
const { SocksProxyAgent } = require('socks-proxy-agent');

const DEFAULT_PROXY_URL = 'http://127.0.0.1:7890';

// ==================== Agent 单例缓存（避免连接风暴） ====================
// 关键点：
// - 以前每次 getFetchProxyOptions()/createHttpProxyAgent() 都 new 一个 Agent
// - 会导致并发请求下产生海量 socket（全部连到 127.0.0.1:7890），最终打爆端口/FD
// - 这里改为“按 proxyUrl 缓存一个 Agent”，并限制 maxSockets
let cachedProxyUrl = null;
let cachedHttpProxyAgent = null;
let cachedSocksProxyAgent = null;

const AGENT_OPTIONS = {
    keepAlive: true,
    keepAliveMsecs: 10_000,
    maxSockets: 128,
    maxFreeSockets: 32,
    scheduling: 'lifo'
};

/**
 * 获取代理配置
 */
function getProxyConfig() {
    // 从环境变量读取代理配置
    const proxy = process.env.HTTPS_PROXY
        || process.env.https_proxy
        || process.env.HTTP_PROXY
        || process.env.http_proxy
        || process.env.PROXY
        || DEFAULT_PROXY_URL;

    if (!proxy) {
        console.log('⚠️  未配置代理,可能无法访问 Telegram API');
        return null;
    }

    console.log(`✅ 检测到代理配置: ${proxy}`);
    return proxy;
}

/**
 * 创建 HTTP/HTTPS 代理 Agent
 */
function createHttpProxyAgent(proxyUrl) {
    if (!proxyUrl) {
        proxyUrl = getProxyConfig();
    }

    if (!proxyUrl) {
        return null;
    }

    try {
        // 命中缓存：复用同一个 Agent，避免连接数指数增长
        if (cachedProxyUrl === proxyUrl) {
            if (proxyUrl.startsWith('socks')) {
                return cachedSocksProxyAgent;
            }
            return cachedHttpProxyAgent;
        }

        // 支持 socks5://
        if (proxyUrl.startsWith('socks')) {
            console.log('✅ 使用 SOCKS 代理');
            cachedProxyUrl = proxyUrl;
            cachedSocksProxyAgent = new SocksProxyAgent(proxyUrl, AGENT_OPTIONS);
            cachedHttpProxyAgent = null;
            return cachedSocksProxyAgent;
        }

        // 支持 http:// 和 https://
        console.log('✅ 使用 HTTP 代理');
        cachedProxyUrl = proxyUrl;
        cachedHttpProxyAgent = new HttpsProxyAgent(proxyUrl, AGENT_OPTIONS);
        cachedSocksProxyAgent = null;
        return cachedHttpProxyAgent;
    } catch (error) {
        console.error('❌ 创建代理 Agent 失败:', error.message);
        return null;
    }
}

/**
 * 为 Telegram Bot 配置代理
 */
function getTelegramBotOptions() {
    // ==================== 可控开关：禁用 polling ====================
    // 背景：在部分环境（Node22 + request 代理链）下，polling 会持续报 EFATAL（href of undefined）。
    // 对于“只发信号、不接收命令”的部署，可以禁用 polling 来避免噪音与无效重试。
    // 启用方式：POLYMARKET_DISABLE_TELEGRAM_POLLING=1
    if (String(process.env.POLYMARKET_DISABLE_TELEGRAM_POLLING || "").trim() === "1") {
        return { polling: false };
    }

    const proxyUrl = getProxyConfig();

    if (!proxyUrl) {
        return {
            polling: true
        };
    }

    // ==================== 兼容策略：禁止 request 自己处理 proxy ====================
    // telegram SDK 底层用 request(@cypress/request)，它会在“自行处理代理”时走 tunnel-agent，
    // 在某些环境下会触发 TypeError（href of undefined）。
    // 本项目已通过 `utils/globalProxy` 注入全局 https/http agent（全局代理），这里强制 `proxy: null`
    // 禁止 request 走 tunnel 分支，让它直接走全局 agent。
    return {
        polling: true,
        request: {
            proxy: null
        }
    };
}

/**
 * 为 fetch/axios 配置代理
 */
function getFetchProxyOptions() {
    const agent = createHttpProxyAgent();

    if (!agent) {
        return {};
    }

    return {
        agent: agent,
        // 给上层调用方一个统一的默认超时（如 fetch/axios 支持）
        timeout: 30000
    };
}

/**
 * 测试代理连接
 */
async function testProxyConnection() {
    const https = require('https');
    const http = require('http');
    const proxyUrl = getProxyConfig();

    if (!proxyUrl) {
        console.log('⚠️  无代理配置,跳过测试');
        return { success: false, message: '无代理配置' };
    }

    return new Promise((resolve) => {
        const agent = createHttpProxyAgent(proxyUrl);

        const options = {
            hostname: 'api.telegram.org',
            port: 443,
            path: '/bot',
            method: 'GET',
            agent: agent,
            timeout: 10000
        };

        const req = https.request(options, (res) => {
            console.log(`✅ 代理连接测试成功 (状态码: ${res.statusCode})`);
            resolve({ success: true, statusCode: res.statusCode });
        });

        req.on('error', (error) => {
            console.error('❌ 代理连接测试失败:', error.message);
            resolve({ success: false, error: error.message });
        });

        req.on('timeout', () => {
            console.error('❌ 代理连接超时');
            req.destroy();
            resolve({ success: false, error: '连接超时' });
        });

        req.end();
    });
}

/**
 * 为 WebSocket 配置代理
 */
function getWebSocketProxyOptions() {
    const proxyUrl = getProxyConfig();

    if (!proxyUrl) {
        return {};
    }

    const agent = createHttpProxyAgent(proxyUrl);

    return {
        agent: agent,
        headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    };
}

module.exports = {
    getProxyConfig,
    createHttpProxyAgent,
    getTelegramBotOptions,
    getFetchProxyOptions,
    getWebSocketProxyOptions,
    testProxyConnection
};
