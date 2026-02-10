/**
 * 全局代理注入 - 在入口文件最开头 require 此文件
 */
const { HttpsProxyAgent } = require('https-proxy-agent');
const http = require('http');
const https = require('https');

const proxy = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.OPINION_PROXY || process.env.DEFAULT_PROXY_URL;
if (!proxy) {
    console.log('🌐 未配置全局代理，使用直连网络');
} else {
    const agent = new HttpsProxyAgent(proxy);

    // 覆盖全局 agent
    http.globalAgent = agent;
    https.globalAgent = agent;

    console.log(`🌐 全局代理已启用: ${proxy}`);
}
