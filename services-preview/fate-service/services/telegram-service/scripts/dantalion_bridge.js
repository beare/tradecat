#!/usr/bin/env node
/**
 * 调用 dantalion-core 生成现代化性格分析
 * 输入参数：
 *   --birth YYYY-MM-DD (日期字符串，忽略时间)
 * 输出：单行 JSON 字符串，如失败输出错误到 stderr 并返回非零
 */
const path = require('path');
const fs = require('fs');

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {};
  for (let i = 0; i < args.length; i += 2) {
    const k = args[i];
    const v = args[i + 1];
    if (k === '--birth') out.birth = v;
  }
  if (!out.birth) {
    throw new Error('usage: dantalion_bridge.js --birth YYYY-MM-DD');
  }
  return out;
}

function main() {
  const { birth } = parseArgs();
  const envPath = process.env.DANTALION_CORE_PATH;
  let corePath = envPath && path.resolve(envPath);
  if (!corePath || !fs.existsSync(corePath)) {
    let current = path.resolve(__dirname);
    while (true) {
      const candidate = path.join(
        current,
        'libs',
        'external',
        'github',
        'dantalion-master',
        'packages',
        'dantalion-core',
        'dist',
        'index.js'
      );
      if (fs.existsSync(candidate)) {
        corePath = candidate;
        break;
      }
      const parent = path.dirname(current);
      if (parent === current) {
        break;
      }
      current = parent;
    }
  }
  if (!corePath || !fs.existsSync(corePath)) {
    throw new Error('dantalion-core not found, please set DANTALION_CORE_PATH');
  }
  const core = require(corePath);
  if (!core.getPersonality) {
    throw new Error('dantalion-core getPersonality not found');
  }
  const result = core.getPersonality(birth);
  if (!result) {
    throw new Error('dantalion-core returned empty result');
  }
  process.stdout.write(JSON.stringify(result));
}

main();
