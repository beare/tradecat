/**
 * MyMemory 翻译服务（备用）
 * 免费，每天 5000 词限制，但不容易被限流
 */

const https = require('https');
const http = require('http');
const { HttpsProxyAgent } = require('https-proxy-agent');
const MYMEMORY_API_BASE = (process.env.MYMEMORY_API_BASE || '').replace(/\/$/, '');

class MyMemoryTranslation {
  constructor() {
    this.baseUrl = MYMEMORY_API_BASE;
    this.proxyUrl = process.env.HTTPS_PROXY || process.env.HTTP_PROXY;
    this.agent = this.proxyUrl ? new HttpsProxyAgent(this.proxyUrl) : null;
  }

  async translate(text, from = 'en', to = 'zh') {
    if (!text || text.length < 2) return text;
    if (!this.baseUrl) throw new Error('未配置 MYMEMORY_API_BASE');
    
    const url = `${this.baseUrl}?q=${encodeURIComponent(text)}&langpair=${from}|${to}`;
    
    return new Promise((resolve, reject) => {
      https.get(url, { timeout: 5000, agent: this.agent }, (res) => {
        let data = '';
        res.on('data', chunk => data += chunk);
        res.on('end', () => {
          try {
            const json = JSON.parse(data);
            const translated = json.responseData?.translatedText;
            // 检查是否是警告消息
            if (translated && !translated.startsWith('MYMEMORY WARNING')) {
              resolve(translated);
            } else {
              reject(new Error('MyMemory 限流'));
            }
          } catch (e) {
            reject(e);
          }
        });
      }).on('error', reject).on('timeout', () => reject(new Error('超时')));
    });
  }
}

module.exports = MyMemoryTranslation;
