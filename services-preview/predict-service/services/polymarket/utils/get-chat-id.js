/**
 * Telegram Chat ID 获取工具
 *
 * 使用方法：
 * 1. 先在Telegram给你的Bot发送一条消息
 * 2. 运行: node get-chat-id.js <你的Bot Token>
 */

const https = require('https');
const TELEGRAM_API_BASE = (process.env.TELEGRAM_API_BASE || '').replace(/\/$/, '');

const token = process.argv[2];

if (!token) {
    console.error('❌ 错误: 缺少Bot Token参数');
    console.log('\n使用方法:');
    console.log('  node get-chat-id.js <你的Bot Token>');
    console.log('\n示例:');
    console.log('  node get-chat-id.js 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz');
    process.exit(1);
}

console.log('🔍 正在获取Chat ID...\n');

if (!TELEGRAM_API_BASE) {
    console.error('❌ 错误: 未配置 TELEGRAM_API_BASE');
    process.exit(1);
}

const url = `${TELEGRAM_API_BASE}/bot${token}/getUpdates`;

https.get(url, (res) => {
    let data = '';

    res.on('data', (chunk) => {
        data += chunk;
    });

    res.on('end', () => {
        try {
            const result = JSON.parse(data);

            if (!result.ok) {
                console.error('❌ API错误:', result.description);
                console.log('\n可能的原因:');
                console.log('  - Bot Token错误');
                console.log('  - Bot已被删除');
                console.log('  - 网络连接问题');
                process.exit(1);
            }

            if (!result.result || result.result.length === 0) {
                console.warn('⚠️ 没有找到任何消息');
                console.log('\n请先:');
                console.log('  1. 在Telegram搜索你的Bot');
                console.log('  2. 点击 /start 或发送任意消息');
                console.log('  3. 然后重新运行此脚本');
                process.exit(0);
            }

            console.log('✅ 找到以下聊天：\n');

            const chats = new Map();

            result.result.forEach((update) => {
                if (update.message && update.message.chat) {
                    const chat = update.message.chat;
                    const key = chat.id;

                    if (!chats.has(key)) {
                        chats.set(key, {
                            id: chat.id,
                            type: chat.type,
                            title: chat.title || `${chat.first_name || ''} ${chat.last_name || ''}`.trim(),
                            username: chat.username || 'N/A'
                        });
                    }
                }
            });

            if (chats.size === 0) {
                console.warn('⚠️ 没有找到聊天信息');
                console.log('\n请确保:');
                console.log('  - 已向Bot发送过消息');
                console.log('  - Bot有权限接收消息');
                process.exit(0);
            }

            let index = 1;
            chats.forEach((chat) => {
                console.log(`${index}. Chat ID: ${chat.id}`);
                console.log(`   类型: ${chat.type}`);
                if (chat.title) console.log(`   名称: ${chat.title}`);
                if (chat.username !== 'N/A') console.log(`   用户名: @${chat.username}`);
                console.log('');
                index++;
            });

            console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
            console.log('\n✅ 下一步：将Chat ID添加到.env文件');
            console.log('\n示例:');
            console.log(`TELEGRAM_BOT_TOKEN=${token}`);
            console.log(`TELEGRAM_CHAT_ID=${Array.from(chats.values())[0].id}`);
            console.log('\n或运行自动配置脚本:');
            console.log('./setup-telegram.sh');

        } catch (error) {
            console.error('❌ 解析响应失败:', error.message);
            console.log('\n原始响应:');
            console.log(data);
            process.exit(1);
        }
    });

}).on('error', (error) => {
    console.error('❌ 网络请求失败:', error.message);
    console.log('\n可能的原因:');
    console.log('  - 网络连接问题');
    console.log('  - Telegram被墙（请使用代理）');
    console.log('\n尝试使用代理:');
    console.log(`  proxychains4 node get-chat-id.js ${token}`);
    process.exit(1);
});
