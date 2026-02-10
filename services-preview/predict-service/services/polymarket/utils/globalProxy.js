/**
 * 全局代理注入 - 在入口文件最开头 require 此文件
 */
const { HttpsProxyAgent } = require('https-proxy-agent');
const http = require('http');
const https = require('https');

const proxy = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.POLYMARKET_PROXY || process.env.DEFAULT_PROXY_URL;
if (!proxy) {
    console.log('🌐 未配置全局代理，使用直连网络');
} else {
    // 关键：限制 socket 数量，避免代理端口连接风暴拖垮整机
    const agent = new HttpsProxyAgent(proxy, {
        keepAlive: true,
        keepAliveMsecs: 10_000,
        maxSockets: 128,
        maxFreeSockets: 32,
        scheduling: 'lifo'
    });

    // 覆盖全局 agent
    http.globalAgent = agent;
    https.globalAgent = agent;

    console.log(`🌐 全局代理已启用: ${proxy}`);
}
