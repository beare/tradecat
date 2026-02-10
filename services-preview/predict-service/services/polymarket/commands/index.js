/**
 * Bot命令处理器
 *
 * 处理所有用户命令：/start, /help, /stats, /settings等
 */

// 市场元数据获取（用于补全名称/slug）
const marketDataFetcher = require('../utils/marketData');
const { t } = require('../i18n');
const POLYMARKET_WEB_BASE = (process.env.POLYMARKET_WEB_BASE || '').replace(/\/$/, '');

class CommandHandler {
    constructor(bot, config, modules, userManager, actions = {}) {
        this.bot = bot;
        this.config = config;
        this.modules = modules;
        this.userManager = userManager;
        this.actions = actions;
        this.chatId = config.telegram.chatId;

        // 检测器引用（用于获取警报历史）
        this.detectors = {};

        // Bot状态（独立控制两种信号）
        this.botState = {
            paused: false,              // 保留全局暂停（用于inline按钮兼容）
            pausedArbitrage: false,     // 套利信号独立控制
            pausedOrderbook: false,     // 订单簿信号独立控制
            pausedClosing: false,       // 扫尾盘信号独立控制
            startTime: Date.now(),
            signalsSent: 0,
            lastSignalTime: null
        };

        // 记录每个用户的主面板消息、快捷键盘状态与通知提示消息
        this.mainPanels = new Map(); // Map<chatId, { messageId, updatedAt }>
        this.replyKeyboardState = new Map(); // Map<chatId, { layout: string, updatedAt }>
        this.notificationPromptMessages = new Map(); // Map<chatId, { arbitrage?: number, orderbook?: number, closing?: number }>
        this.rateLimitTimers = new Map(); // Map<string, NodeJS.Timeout>

        // 定时清理键盘缓存，避免长时间运行导致内存膨胀
        this.replyKeyboardPruneTimer = setInterval(() => this.pruneReplyKeyboard(), 30 * 60 * 1000);
        if (this.replyKeyboardPruneTimer.unref) {
            this.replyKeyboardPruneTimer.unref(); // 不中断进程退出
        }

        console.log('✅ 命令处理器初始化完成');
    }

    // ==================== 通用对齐工具 ====================
    // 视觉宽度计算：emoji和中文算2，ASCII算1
    _vw(str) {
        let w = 0;
        for (const c of str) {
            const code = c.codePointAt(0);
            w += (code > 0x2000) ? 2 : 1;
        }
        return w;
    }
    // 左对齐填充
    _ljust(str, width) { return str + ' '.repeat(Math.max(0, width - this._vw(str))); }
    // 右对齐填充
    _rjust(str, width) { return ' '.repeat(Math.max(0, width - this._vw(str))) + str; }
    // 通用对齐算法：前N列左对齐，其余右对齐，列间单空格
    _alignRows(rows, leftAlignCols = 1) {
        if (!rows.length) return [];
        const colCnt = Math.max(...rows.map(r => r.length));
        const widths = [];
        for (let i = 0; i < colCnt; i++) {
            widths[i] = Math.max(...rows.map(r => this._vw(r[i] || '')));
        }
        return rows.map(row => {
            const cells = [];
            for (let i = 0; i < colCnt; i++) {
                const cell = row[i] || '';
                cells.push(i < leftAlignCols ? this._ljust(cell, widths[i]) : this._rjust(cell, widths[i]));
            }
            return cells.join(' ');
        });
    }

    /**
     * 注册所有命令
     */
    registerCommands() {
        // /start 命令
        this.bot.onText(/\/start/, (msg) => {
            this.handleStart(msg);
        });

        // /help 命令
        this.bot.onText(/\/help/, (msg) => this.handleHelp(msg));

        // /stats 命令
        this.bot.onText(/\/stats/, (msg) => this.handleStats(msg));

        // /status 命令
        this.bot.onText(/\/status/, (msg) => this.handleStatus(msg));

        // /settings 命令
        this.bot.onText(/\/settings/, (msg) => this.handleSettings(msg));

        // /closing 命令 - 最新扫尾盘
        this.bot.onText(/\/closing/, (msg) => this.handleClosingLatest(msg));

        // /preset 命令 - 预设配置（参数可选）
        this.bot.onText(/\/preset(?:\s+(\w+))?/, (msg, match) => {
            const mode = match && match[1] ? match[1] : null;
            this.handlePreset(msg, mode);
        });

        // /subscribe 命令 - 订阅信号
        this.bot.onText(/\/subscribe/, (msg) => this.handleSubscribe(msg));

        // /unsubscribe 命令 - 取消订阅
        this.bot.onText(/\/unsubscribe/, (msg) => this.handleUnsubscribe(msg));

        // /csv 命令 - 生成CSV报告（全员可用）
        this.bot.onText(/\/csv/, (msg) => this.handleCsvReport(msg));

        console.log('✅ 命令处理器已绑定（统一面板模式）');
    }

    /**
     * /start 命令 - 欢迎消息
     */
    async handleStart(msg, options = {}) {
        const chatId = msg.chat.id;
        console.log(`🚀 [handleStart] chatId=${chatId}`);

        // 注册用户
        this.userManager.registerUser(chatId, msg.from);

        let flashMessage = options.flashMessage || null;
        if (!flashMessage && options.section) {
            const hints = {
                help: '帮助：下方按钮已经列出所有操作入口。',
                notifications: '提示：直接使用下方按钮切换通知和阈值。',
                modules: '提示：可以在下方按钮启用或禁用模块。',
                stats: '提示：统计数据已在面板中展示。'
            };
            flashMessage = hints[options.section] || null;
        }

        try {
            await this.showMainPanel(chatId, {
                messageId: options.messageId,
                forceNew: options.forceNew ?? true,
                flashMessage,
                forceKeyboardRefresh: options.forceKeyboardRefresh ?? true
            });
            console.log(`✅ [handleStart] 主面板已发送 chatId=${chatId}`);
        } catch (err) {
            console.error(`❌ [handleStart] 发送主面板失败 chatId=${chatId}:`, err.message);
        }
    }

    /**
     * /help 命令 - 帮助信息
     */
    async handleHelp(msg) {
        // 并行发送帮助和面板
        Promise.all([
            this.sendHelpMessage(msg.chat.id),
            this.showMainPanel(msg.chat.id, { flashMessage: '帮助：下方按钮覆盖通知、阈值、模块、预设、订阅等全部设置。' })
        ]).catch(() => {});
    }

    /**
     * /stats 命令 - 统计信息
     */
    async handleStats(msg, options = {}) {
        await this.showMainPanel(msg.chat.id, {
            messageId: options.messageId,
            forceNew: options.forceNew,
            flashMessage: options.flashMessage || '统计摘要已更新。'
        });
    }

    /**
     * /status 命令 - Bot状态（显示独立控制状态）
     */
    async handleStatus(msg, options = {}) {
        await this.showMainPanel(msg.chat.id, {
            messageId: options.messageId,
            forceNew: options.forceNew,
            flashMessage: options.flashMessage
        });
    }

    /**
     * /settings 命令 - 设置
     */
    async handleSettings(msg) {
        await this.showMainPanel(msg.chat.id, {
            flashMessage: '请在下方直接切换通知开关与阈值档位。'
        });
    }

    /**
     * /modules 命令 - 模块状态
     */
    async handleModules(msg, options = {}) {
        await this.showMainPanel(msg.chat.id, {
            messageId: options.messageId,
            forceNew: options.forceNew,
            flashMessage: options.flashMessage || '全局模块开关位于下方按钮。'
        });
    }

    /**
     * /closing 命令 - 最新扫尾盘
     */
    async handleClosingLatest(msg, options = {}) {
        const chatId = msg.chat.id;
        const replyTo = options.replyTo ?? msg.message_id;

        if (this.actions?.sendLatestClosing) {
            await this.actions.sendLatestClosing({
                chatId,
                replyTo
            });
            return;
        }

        await this.bot.sendMessage(chatId, '⚠️ 扫尾盘模块未启用。', {
            reply_to_message_id: replyTo
        });
    }

    /**
     * 渲染统一主面板（默认在原消息内刷新）
     */
    async showMainPanel(chatId, options = {}) {
        if (!this.userManager.getUserInfo(chatId)) {
            this.userManager.registerUser(chatId);
        }

        const plan = this.buildMainPanelPlan(chatId, options);
        console.log(`📨 [MainPanel] chat=${chatId} needNew=${plan.needNewMessage} msgId=${plan.messageId || 'none'} forceNew=${options.forceNew === true}`);

        if (plan.shouldRefreshBefore) {
            plan.refreshKeyboard(true).catch(() => {}); // 异步刷新，不阻塞主面板发送
        }

        if (plan.needNewMessage) {
            return this.sendMainPanelMessage({
                chatId,
                content: plan.content,
                basePayload: plan.basePayload,
                options,
                refreshAfterSend: () => {}
            });
        }

        return this.editMainPanelMessage({
            chatId,
            content: plan.content,
            basePayload: { ...plan.basePayload, message_id: plan.messageId },
            options,
            messageId: plan.messageId,
            refreshKeyboard: plan.refreshKeyboard
        });
    }

    buildMainPanelPlan(chatId, options = {}) {
        const storedPanel = this.mainPanels.get(chatId);
        const messageId = options.messageId || storedPanel?.messageId;
        const forceKeyboardRefresh = options.forceKeyboardRefresh === true;
        const needNewMessage = options.forceNew === true || !messageId;

        const content = this.buildMainPanelContent(chatId, options.flashMessage);
        const basePayload = {
            chat_id: chatId,
            disable_web_page_preview: true,
            reply_markup: { inline_keyboard: content.keyboard }
        };

        return {
            messageId,
            needNewMessage,
            content,
            basePayload,
            refreshKeyboard: (force = false) => this.safeRefreshKeyboard(chatId, force),
            shouldRefreshBefore: forceKeyboardRefresh
        };
    }

    async safeRefreshKeyboard(chatId, force) {
        try {
            await this.ensureReplyKeyboard(chatId, force);
        } catch (err) {
            console.warn(`⚠️ 常驻键盘刷新失败: ${err.message}`);
        }
    }

    async editMainPanelMessage({ chatId, content, basePayload, options, messageId, refreshKeyboard }) {
        try {
            console.log(`📨 [MainPanel] edit attempt (Markdown) chat=${chatId} messageId=${messageId}`);
            await this.bot.editMessageText(content.text, {
                ...basePayload,
                parse_mode: 'Markdown'
            });
            this.rememberMainPanel(chatId, messageId);
            refreshKeyboard(false).catch(() => {});
            console.log(`✅ [MainPanel] edit success chat=${chatId}`);
            return { messageId, replaced: false };
        } catch (error) {
            const description = error?.response?.body?.description || error.message;
            if (this.isMarkdownParseError(description)) {
                try {
                    console.warn(`⚠️ [MainPanel] edit Markdown失败，尝试纯文本回退: ${description}`);
                    await this.bot.editMessageText(this.stripMarkdown(content.text), basePayload);
                    this.rememberMainPanel(chatId, messageId);
                    refreshKeyboard(false).catch(() => {});
                    console.warn('⚠️ Markdown解析失败，已回退为纯文本主面板消息');
                    return { messageId, replaced: false, fallback: true };
                } catch (fallbackError) {
                    const fallbackDesc = fallbackError?.response?.body?.description || fallbackError.message;
                    console.warn(`⚠️ 主面板Markdown回退失败，准备重新发送: ${fallbackDesc}`);
                }
            } else if (this.isRateLimitError(description)) {
                const retryAfterMs = this.getRetryAfterSeconds(error) * 1000;
                console.warn(`⚠️ Telegram 限频，${Math.ceil(retryAfterMs / 1000)} 秒后重试主面板编辑 (chat=${chatId})`);
                this.scheduleRateLimitRetry(`mainPanel:${chatId}`, retryAfterMs, () => this.showMainPanel(chatId, {
                    ...options,
                    forceNew: false,
                    forceKeyboardRefresh: false
                }));
                return { messageId, replaced: false, retryScheduled: true };
            } else {
                // 编辑失败（消息不存在等），清除缓存，下面会发送新消息
                console.warn(`⚠️ 更新主面板失败，将重新发送: ${description}`);
                this.mainPanels.delete(chatId);
            }
        }

        // 回退到发送新消息
        return this.sendMainPanelMessage({
            chatId,
            content,
            basePayload: { ...basePayload, message_id: undefined },
            options,
            refreshAfterSend: () => {}
        });
    }

    async sendMainPanelMessage({ chatId, content, basePayload, options, refreshAfterSend }) {
        const payload = { ...basePayload };
        if (Object.prototype.hasOwnProperty.call(payload, 'message_id') && payload.message_id == null) {
            delete payload.message_id;
        }
        try {
            console.log(`📨 [MainPanel] send attempt (Markdown) chat=${chatId}`);
            const sent = await this.bot.sendMessage(chatId, content.text, {
                ...payload,
                parse_mode: 'Markdown'
            });

            this.rememberMainPanel(chatId, sent.message_id);
            refreshAfterSend();
            console.log(`✅ [MainPanel] send success chat=${chatId} messageId=${sent.message_id}`);
            return { messageId: sent.message_id, replaced: true };
        } catch (error) {
            const description = error?.response?.body?.description || error.message;

            if (this.isMarkdownParseError(description)) {
                const plainText = this.stripMarkdown(content.text);
                try {
                    console.warn(`⚠️ [MainPanel] send Markdown失败，尝试纯文本回退: ${description}`);
                    const sent = await this.bot.sendMessage(chatId, plainText, basePayload);
                    this.rememberMainPanel(chatId, sent.message_id);
                    refreshAfterSend();
                    console.warn('⚠️ Markdown解析失败，主面板已回退为纯文本消息');
                    return { messageId: sent.message_id, replaced: true, fallback: true };
                } catch (fallbackError) {
                    const fallbackDesc = fallbackError?.response?.body?.description || fallbackError.message;
                    console.error(`❌ 主面板纯文本回退也失败: ${fallbackDesc}`);
                    throw fallbackError;
                }
            } else if (this.isRateLimitError(description)) {
                const retryAfterMs = this.getRetryAfterSeconds(error) * 1000;
                console.warn(`⚠️ Telegram 限频，${Math.ceil(retryAfterMs / 1000)} 秒后重试主面板发送 (chat=${chatId})`);
                this.scheduleRateLimitRetry(`mainPanel:${chatId}`, retryAfterMs, () => this.showMainPanel(chatId, {
                    ...options,
                    forceNew: true,
                    forceKeyboardRefresh: false
                }));
                return { messageId: null, replaced: false, retryScheduled: true };
            }

            console.error(`❌ 发送主面板失败: ${description}`);
            throw error;
        }
    }

    buildStatusTable(chatId) {
        const lang = this.userManager.getLang(chatId);
        const i18n = t(lang);
        const m = i18n.panel.modules;
        
        const thresholds = this.userManager.getThresholdSummary(chatId) || this.getDefaultThresholdSummary();
        const notifications = this.userManager.getNotificationSettings(chatId) || { arbitrage: true, orderbook: true, closing: true, largeTrade: true, smartMoney: true, newMarket: true };
        const bellIcon = (on) => on ? '🔔' : '🔕';
        const rows = [
            [m.arbitrage, bellIcon(notifications.arbitrage), thresholds.arbitrage.icon],
            [m.orderbook, bellIcon(notifications.orderbook), thresholds.orderbook.icon],
            [m.closing, bellIcon(notifications.closing), thresholds.closing.icon],
            [m.largeTrade, bellIcon(notifications.largeTrade), thresholds.largeTrade.icon],
            [m.smartMoney, bellIcon(notifications.smartMoney), thresholds.smartMoney.icon],
            [m.newMarket, bellIcon(notifications.newMarket), '']
        ];
        const fmt = this._alignRows(rows, 1);
        return '```\n' + fmt.join('\n') + '\n```';
    }

    buildMainPanelContent(chatId, flashMessage = null) {
        const lang = this.userManager.getLang(chatId);
        const i18n = t(lang);
        
        const thresholds = this.userManager.getThresholdSummary(chatId) || this.getDefaultThresholdSummary();
        const notifications = this.userManager.getNotificationSettings(chatId) || { arbitrage: true, orderbook: true, closing: true, largeTrade: true, smartMoney: true, newMarket: true };

        const bellIcon = (on) => on ? '🔔' : '🔕';
        const nowStr = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }).replace(/\//g, '-');
        const m = i18n.panel.modules;

        const rows = [
            [m.arbitrage, bellIcon(notifications.arbitrage), thresholds.arbitrage.icon],
            [m.orderbook, bellIcon(notifications.orderbook), thresholds.orderbook.icon],
            [m.closing, bellIcon(notifications.closing), thresholds.closing.icon],
            [m.largeTrade, bellIcon(notifications.largeTrade), thresholds.largeTrade.icon],
            [m.smartMoney, bellIcon(notifications.smartMoney), thresholds.smartMoney.icon],
            [m.newMarket, bellIcon(notifications.newMarket), '']
        ];

        const fmt = this._alignRows(rows, 1);

        const lines = [
            i18n.panel.title,
            i18n.panel.moduleNotifThreshold,
            '```',
            ...fmt,
            '```',
            `⏱️ ${i18n.panel.beijingTime}：${nowStr}`
        ];

        // 语言切换按钮（替换原来的显示模式按钮）
        const langBtn = lang === 'en'
            ? { text: '🌐 中文', callback_data: 'set_lang_zh-CN' }
            : { text: '🌐 EN', callback_data: 'set_lang_en' };

        const keyboard = [
            [
                { text: i18n.panel.closing, callback_data: 'show_closing_latest' },
                { text: i18n.panel.threshold, callback_data: 'menu_thresholds' },
                { text: i18n.panel.notification, callback_data: 'menu_notifications' },
                langBtn
            ]
        ];

        return {
            text: lines.join('\n'),
            keyboard
        };
    }

    buildThresholdButton(type, level, active, icon, lang = 'zh-CN') {
        const i18n = t(lang);
        const levelNames = { 1: i18n.panel.loose, 2: i18n.panel.medium, 3: i18n.panel.strict };
        return {
            text: `${icon} ${levelNames[level]}${active ? ' ✅' : ''}`,
            callback_data: `threshold_${type}_${level}`
        };
    }

    buildReplyKeyboard(chatId) {
        const lang = this.userManager.getLang(chatId);
        const i18n = t(lang);
        const langBtn = lang === 'en' ? '🌐 中文' : '🌐 EN';
        const help = lang === 'en' ? '❓ Help' : '❓ 帮助';
        const main = lang === 'en' ? '🏠 Menu' : '🏠 主菜单';
        return {
            keyboard: [
                [i18n.panel.closing, i18n.panel.threshold, i18n.panel.notification, i18n.panel.stats, langBtn],
                [main, help]
            ],
            resize_keyboard: true,
            is_persistent: true
        };
    }

    async renderNotificationMenu(chatId, messageId = null) {
        const lang = this.userManager.getLang(chatId);
        const i18n = t(lang);
        
        const statusTable = this.buildStatusTable(chatId);
        const notifText = `${i18n.panel.notificationTitle}\n${statusTable}\n${i18n.panel.notificationHint}`;
        const notifKeyboard = {
            inline_keyboard: [
                [
                    this.buildNotificationToggle('arbitrage', this.userManager.isNotificationEnabled(chatId, 'arbitrage'), lang),
                    this.buildNotificationToggle('orderbook', this.userManager.isNotificationEnabled(chatId, 'orderbook'), lang),
                    this.buildNotificationToggle('closing', this.userManager.isNotificationEnabled(chatId, 'closing'), lang)
                ],
                [
                    this.buildNotificationToggle('largeTrade', this.userManager.isNotificationEnabled(chatId, 'largeTrade'), lang),
                    this.buildNotificationToggle('smartMoney', this.userManager.isNotificationEnabled(chatId, 'smartMoney'), lang),
                    this.buildNotificationToggle('newMarket', this.userManager.isNotificationEnabled(chatId, 'newMarket'), lang)
                ],
                [{ text: i18n.panel.backToMain, callback_data: 'show_main_menu' }]
            ]
        };

        if (messageId) {
            try {
                await this.bot.editMessageText(notifText, { chat_id: chatId, message_id: messageId, reply_markup: notifKeyboard, parse_mode: 'Markdown' });
                return;
            } catch (err) {
                if (!err.message?.includes('message to edit not found') && !err.message?.includes('message is not modified')) {
                    console.warn(`⚠️ 更新通知菜单失败: ${err.message}`);
                }
            }
        }

        await this.bot.sendMessage(chatId, notifText, { reply_markup: notifKeyboard, parse_mode: 'Markdown' });
    }

    async renderThresholdMenu(chatId, messageId = null) {
        const lang = this.userManager.getLang(chatId);
        const i18n = t(lang);
        
        const statusTable = this.buildStatusTable(chatId);
        const threshText = `${i18n.panel.thresholdTitle}\n${statusTable}\n${i18n.panel.thresholdHint}`;
        const threshKeyboard = {
            inline_keyboard: [
                [
                    this.buildThresholdButton('arbitrage', 1, this.userManager.getThreshold(chatId, 'arbitrage') === 1, '💼', lang),
                    this.buildThresholdButton('arbitrage', 2, this.userManager.getThreshold(chatId, 'arbitrage') === 2, '💼', lang),
                    this.buildThresholdButton('arbitrage', 3, this.userManager.getThreshold(chatId, 'arbitrage') === 3, '💼', lang)
                ],
                [
                    this.buildThresholdButton('orderbook', 1, this.userManager.getThreshold(chatId, 'orderbook') === 1, '📚', lang),
                    this.buildThresholdButton('orderbook', 2, this.userManager.getThreshold(chatId, 'orderbook') === 2, '📚', lang),
                    this.buildThresholdButton('orderbook', 3, this.userManager.getThreshold(chatId, 'orderbook') === 3, '📚', lang)
                ],
                [
                    this.buildThresholdButton('closing', 1, this.userManager.getThreshold(chatId, 'closing') === 1, '⏰', lang),
                    this.buildThresholdButton('closing', 2, this.userManager.getThreshold(chatId, 'closing') === 2, '⏰', lang),
                    this.buildThresholdButton('closing', 3, this.userManager.getThreshold(chatId, 'closing') === 3, '⏰', lang)
                ],
                [
                    this.buildThresholdButton('largeTrade', 1, this.userManager.getThreshold(chatId, 'largeTrade') === 1, '💸', lang),
                    this.buildThresholdButton('largeTrade', 2, this.userManager.getThreshold(chatId, 'largeTrade') === 2, '💸', lang),
                    this.buildThresholdButton('largeTrade', 3, this.userManager.getThreshold(chatId, 'largeTrade') === 3, '💸', lang)
                ],
                [
                    this.buildThresholdButton('smartMoney', 1, this.userManager.getThreshold(chatId, 'smartMoney') === 1, '🧠', lang),
                    this.buildThresholdButton('smartMoney', 2, this.userManager.getThreshold(chatId, 'smartMoney') === 2, '🧠', lang),
                    this.buildThresholdButton('smartMoney', 3, this.userManager.getThreshold(chatId, 'smartMoney') === 3, '🧠', lang)
                ],
                [{ text: i18n.panel.backToMain, callback_data: 'show_main_menu' }]
            ]
        };

        if (messageId) {
            try {
                await this.bot.editMessageText(threshText, { chat_id: chatId, message_id: messageId, reply_markup: threshKeyboard, parse_mode: 'Markdown' });
                return;
            } catch (err) {
                if (!err.message?.includes('message to edit not found')) {
                    console.warn(`⚠️ 更新阈值菜单失败: ${err.message}`);
                }
            }
        }

        await this.bot.sendMessage(chatId, threshText, { reply_markup: threshKeyboard, parse_mode: 'Markdown' });
    }

    async ensureReplyKeyboard(chatId, force = false) {
        const layout = this.buildReplyKeyboard(chatId);
        const serialized = JSON.stringify(layout);
        const cached = this.replyKeyboardState.get(chatId);

        if (!force && cached?.layout === serialized) {
            return;
        }

        this.rememberReplyKeyboard(chatId, serialized);

        const lang = this.userManager.getLang(chatId);
        const placeholderText = lang === 'en' ? 'Hello 👋' : '你好👋';

        try {
            await this.bot.sendMessage(chatId, placeholderText, {
                reply_markup: layout,
                disable_notification: true
            });
        } catch (error) {
            const description = error?.response?.body?.description || error.message;
            if (description?.includes('text must be non-empty')) {
                console.warn('⚠️ 更新常驻键盘失败: Telegram 要求非空文本，已跳过刷新。');
                return;
            }
            if (this.isRateLimitError(description)) {
                const retryAfterMs = this.getRetryAfterSeconds(error) * 1000;
                console.warn(`⚠️ 常驻键盘触发限频，${Math.ceil(retryAfterMs / 1000)} 秒后重试 (chat=${chatId})`);
                this.scheduleRateLimitRetry(`replyKeyboard:${chatId}`, retryAfterMs, () => this.ensureReplyKeyboard(chatId, true));
                return;
            }
            console.warn(`⚠️ 更新常驻键盘失败: ${description}`);
        }
    }

    async sendHelpMessage(chatId) {
        const lang = this.userManager.getLang(chatId);
        const nowStr = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }).replace(/\//g, '-');
        
        const helpLines = lang === 'en' ? [
            '❓ Help',
            '```',
            '📢 Notifications - Toggle module alerts',
            '🎚️ Threshold - Adjust sensitivity',
            '📋 Closing - View latest signals',
            '```',
            `⏱️ Beijing Time: ${nowStr}`
        ] : [
            '❓ 使用帮助',
            '```',
            '📢 通知开关 - 开/关各模块推送',
            '🎚️ 阈值 - 调整灵敏度',
            '📋 扫尾盘 - 查看最新信号',
            '```',
            `⏱️ 北京时间：${nowStr}`
        ];

        const backBtn = lang === 'en' ? '🏠 Back to Menu' : '🏠 返回主菜单';

        await this.bot.sendMessage(chatId, helpLines.join('\n'), {
            parse_mode: 'Markdown',
            disable_web_page_preview: true,
            reply_markup: {
                inline_keyboard: [[
                    { text: backBtn, callback_data: 'show_main_panel' }
                ]]
            }
        });
    }

    /**
     * 设置检测器引用
     */
    setDetectors(detectors) {
        this.detectors = detectors;
    }

    /**
     * 截断字符串
     */
    truncate(str, max = 11) {
        if (!str) return '未知';
        return str.length > max ? str.slice(0, max) + '..' : str;
    }

    /**
     * 格式化时间差
     */
    formatTimeAgo(ms) {
        const s = Math.floor(ms / 1000);
        if (s < 60) return `${s}s`;
        const m = Math.floor(s / 60);
        if (m < 60) return `${m}m`;
        const h = Math.floor(m / 60);
        return `${h}h`;
    }

    /**
     * 渲染信号历史面板
     */
    async renderAlertPanel(chatId, messageId = null) {
        const now = Date.now();
        const nowStr = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }).replace(/\//g, '-');

        // 收集所有警报历史
        let allAlerts = [];
        if (this.detectors.arbitrage?.getAlertHistory) {
            allAlerts = allAlerts.concat(this.detectors.arbitrage.getAlertHistory());
        }
        if (this.detectors.orderbook?.getAlertHistory) {
            allAlerts = allAlerts.concat(this.detectors.orderbook.getAlertHistory());
        }
        if (this.detectors.closing?.getAlertHistory) {
            allAlerts = allAlerts.concat(this.detectors.closing.getAlertHistory());
        }

        // 补全缺失的名称/slug（避免显示 0x...）
        const enriched = await Promise.all(allAlerts.map(async (alert) => {
            if (!alert?.market) return alert;

            const needsName = !alert.name || alert.name.startsWith('0x');
            const needsSlug = !alert.slug;
            const needsEventSlug = !alert.eventSlug;

            if (!(needsName || needsSlug || needsEventSlug)) {
                return alert;
            }

            const [name, slug, eventSlug] = await Promise.all([
                needsName ? marketDataFetcher.getMarketName(alert.market) : Promise.resolve(alert.name),
                needsSlug ? marketDataFetcher.getMarketSlug(alert.market) : Promise.resolve(alert.slug),
                needsEventSlug ? marketDataFetcher.getEventSlug(alert.market) : Promise.resolve(alert.eventSlug)
            ]);

            if (needsName && name) alert.name = name;
            if (needsSlug && slug) alert.slug = slug;
            if (needsEventSlug && eventSlug) alert.eventSlug = eventSlug;

            return alert;
        }));

        allAlerts = enriched;

        // 按时间排序（最旧在上，最新在下）
        allAlerts.sort((a, b) => a.time - b.time);

        // 构建消息
        let lines = ['🔫 颗秒版', '市场/距上次/值', '```'];
        
        if (allAlerts.length === 0) {
            lines.push('暂无信号记录');
        } else {
            for (const alert of allAlerts) {
                const name = this.truncate(alert.name, 11).padEnd(13);
                const ago = this.formatTimeAgo(now - alert.time).padEnd(4);
                lines.push(`${alert.icon} ${name} ${ago} ${alert.value}`);
            }
        }
        
        lines.push('```');
        lines.push(`⏱️ 北京时间：${nowStr}`);

        const text = lines.join('\n');

        // 获取最新市场的链接
        let latestUrl = POLYMARKET_WEB_BASE || null;
        if (allAlerts.length > 0 && POLYMARKET_WEB_BASE) {
            const latest = allAlerts[allAlerts.length - 1];
            if (latest.eventSlug && latest.slug) {
                latestUrl = `${POLYMARKET_WEB_BASE}/event/${latest.eventSlug}?market=${latest.slug}`;
            } else if (latest.slug) {
                latestUrl = `${POLYMARKET_WEB_BASE}/market/${latest.slug}`;
            }
        }

        const keyboard = latestUrl
            ? {
                inline_keyboard: [
                    [{ text: '🔗 查看最新市场', url: latestUrl }]
                ]
            }
            : undefined;

        if (messageId) {
            try {
                await this.bot.editMessageText(text, { chat_id: chatId, message_id: messageId, reply_markup: keyboard, parse_mode: 'Markdown' });
                return;
            } catch (err) {
                if (!err.message?.includes('message is not modified')) {
                    console.warn(`⚠️ 更新警报面板失败: ${err.message}`);
                }
            }
        }

        await this.bot.sendMessage(chatId, text, { reply_markup: keyboard, parse_mode: 'Markdown' });
    }

    async sendNotificationPrompt(chatId, type) {
        const enabled = this.userManager.isNotificationEnabled(chatId, type);
        const config = {
            arbitrage: {
                icon: '💼',
                name: '套利',
                enableText: '🔔 开启 ✅',
                disableText: '🔕 关闭 ❌',
                enableAction: 'notification_arbitrage_enable',
                disableAction: 'notification_arbitrage_disable'
            },
            orderbook: {
                icon: '📚',
                name: '订单簿',
                enableText: '🔔 开启 ✅',
                disableText: '🔕 关闭 ❌',
                enableAction: 'notification_orderbook_enable',
                disableAction: 'notification_orderbook_disable'
            },
            closing: {
                icon: '⏰',
                name: '扫尾盘',
                enableText: '🔔 开启 ✅',
                disableText: '🔕 关闭 ❌',
                enableAction: 'notification_closing_enable',
                disableAction: 'notification_closing_disable'
            }
        }[type];

        if (!config) return;

        const text = `${config.icon} *${config.name}通知状态*

当前：${enabled ? '✅ 已开启' : '❌ 已关闭'}

点击下方按钮可立即切换。`;

        const keyboard = {
            inline_keyboard: [
                [
                    {
                        text: config.enableText,
                        callback_data: config.enableAction
                    },
                    {
                        text: config.disableText,
                        callback_data: config.disableAction
                    }
                ],
                [
                    { text: '🔙 返回主面板', callback_data: 'refresh_main' }
                ]
            ]
        };

        const prompts = this.notificationPromptMessages.get(chatId) || {};
        const messageId = prompts[type];

        try {
            if (messageId) {
                await this.bot.editMessageText(text, {
                    chat_id: chatId,
                    message_id: messageId,
                    parse_mode: 'Markdown',
                    reply_markup: keyboard
                });
            } else {
                const sent = await this.bot.sendMessage(chatId, text, {
                    parse_mode: 'Markdown',
                    reply_markup: keyboard
                });
                prompts[type] = sent.message_id;
                this.notificationPromptMessages.set(chatId, prompts);
                return;
            }
        } catch (error) {
            console.warn(`⚠️ 更新${type}通知提示失败: ${error.message}`);
            try {
                const sent = await this.bot.sendMessage(chatId, text, {
                    parse_mode: 'Markdown',
                    reply_markup: keyboard
                });
                prompts[type] = sent.message_id;
                this.notificationPromptMessages.set(chatId, prompts);
            } catch (err) {
                console.error(`❌ 发送${type}通知提示失败: ${err.message}`);
            }
            return;
        }

        this.notificationPromptMessages.set(chatId, prompts);
    }

    buildDisplayModeToggle(chatId) {
        const mode = this.userManager.getDisplayMode(chatId);
        const isCompact = mode === 'compact';
        return {
            text: isCompact ? '🔫 颗秒版 ✅' : '📝 详细版 ✅',
            callback_data: 'toggle_display_mode'
        };
    }

    buildNotificationToggle(type, enabled, lang = 'zh-CN') {
        const i18n = t(lang);
        const name = i18n.panel.modules[type] || type;
        return {
            text: enabled ? `✅ ${name}` : `❌ ${name}`,
            callback_data: `toggle_notification_${type}`
        };
    }

    getDefaultThresholdSummary() {
        return {
            arbitrage: { level: 2, icon: '🟡', name: '中等', threshold: '4.0%' },
            orderbook: { level: 2, icon: '🟡', name: '中等', threshold: '6x + $100K' },
            closing: { level: 2, icon: '🟡', name: '中等', threshold: '仅中/高置信度' },
            largeTrade: { level: 2, icon: '🟡', name: '中等', threshold: '$5K' },
            smartMoney: { level: 2, icon: '🟡', name: '中等', threshold: '$500' },
            newMarket: { level: 1, icon: '', name: '', threshold: '' }
        };
    }

    getUserLabel(chatId, userInfo) {
        if (userInfo.username) {
            return this.escapeMarkdown(`@${userInfo.username}`);
        }
        const names = [userInfo.firstName, userInfo.lastName].filter(Boolean).join(' ').trim();
        if (names) {
            return this.escapeMarkdown(names);
        }
        return this.escapeMarkdown(`ID ${chatId}`);
    }

    formatUptime(startTime) {
        const diff = Date.now() - startTime;
        const hours = Math.floor(diff / 3600000);
        const minutes = Math.floor((diff % 3600000) / 60000);
        return `${hours}小时 ${minutes}分钟`;
    }

    formatTimestamp(timestamp) {
        const date = new Date(timestamp);
        return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}:${date.getSeconds().toString().padStart(2, '0')}`;
    }

    async handlePanelAction(action, context = {}) {
        const chatId = context.chatId;
        const messageId = context.messageId;

        // 处理扫尾盘分页按钮（格式：closing_page_N）
        if (action.startsWith('closing_page_')) {
            const pageMatch = action.match(/^closing_page_(\d+)$/);
            if (pageMatch) {
                const page = parseInt(pageMatch[1], 10);
                if (this.actions?.updateClosingPage) {
                    await this.actions.updateClosingPage({ chatId, messageId, page });
                }
                return true;
            }
        }

        switch (action) {
            case 'toggle_notification_arbitrage': {
                this.userManager.toggleNotification(chatId, 'arbitrage');
                if (messageId) {
                    this.renderNotificationMenu(chatId, messageId).catch(() => {});
                }
                return true;
            }

            case 'toggle_notification_orderbook': {
                this.userManager.toggleNotification(chatId, 'orderbook');
                if (messageId) {
                    this.renderNotificationMenu(chatId, messageId).catch(() => {});
                }
                return true;
            }

            case 'toggle_notification_closing': {
                this.userManager.toggleNotification(chatId, 'closing');
                if (messageId) {
                    this.renderNotificationMenu(chatId, messageId).catch(() => {});
                }
                return true;
            }

            case 'toggle_notification_largeTrade': {
                this.userManager.toggleNotification(chatId, 'largeTrade');
                if (messageId) {
                    this.renderNotificationMenu(chatId, messageId).catch(() => {});
                }
                return true;
            }

            case 'toggle_notification_smartMoney': {
                this.userManager.toggleNotification(chatId, 'smartMoney');
                if (messageId) {
                    this.renderNotificationMenu(chatId, messageId).catch(() => {});
                }
                return true;
            }

            case 'toggle_notification_newMarket': {
                this.userManager.toggleNotification(chatId, 'newMarket');
                if (messageId) {
                    this.renderNotificationMenu(chatId, messageId).catch(() => {});
                }
                return true;
            }

            // toggle_display_mode 已禁用，使用语言切换替代
            // case 'toggle_display_mode': { ... }

            case 'menu_notifications': {
                this.renderNotificationMenu(chatId, messageId).catch(() => {});
                return true;
            }

            case 'menu_thresholds': {
                this.renderThresholdMenu(chatId, messageId).catch(() => {});
                return true;
            }

            case 'show_closing_latest': {
                if (this.actions?.sendLatestClosing) {
                    await this.actions.sendLatestClosing({ chatId, replyTo: messageId });
                } else {
                    await this.bot.sendMessage(chatId, '⚠️ 功能未启用', { reply_to_message_id: messageId });
                }
                return true;
            }

            case 'threshold_arbitrage_1':
            case 'threshold_arbitrage_2':
            case 'threshold_arbitrage_3': {
                const level = Number(action.split('_').pop());
                this.userManager.setThreshold(chatId, 'arbitrage', level);
                this.renderThresholdMenu(chatId, messageId).catch(() => {});
                return true;
            }

            case 'threshold_orderbook_1':
            case 'threshold_orderbook_2':
            case 'threshold_orderbook_3': {
                const level = Number(action.split('_').pop());
                this.userManager.setThreshold(chatId, 'orderbook', level);
                this.renderThresholdMenu(chatId, messageId).catch(() => {});
                return true;
            }

            case 'threshold_closing_1':
            case 'threshold_closing_2':
            case 'threshold_closing_3': {
                const level = Number(action.split('_').pop());
                this.userManager.setThreshold(chatId, 'closing', level);
                this.renderThresholdMenu(chatId, messageId).catch(() => {});
                return true;
            }

            case 'threshold_largeTrade_1':
            case 'threshold_largeTrade_2':
            case 'threshold_largeTrade_3': {
                const level = Number(action.split('_').pop());
                this.userManager.setThreshold(chatId, 'largeTrade', level);
                this.renderThresholdMenu(chatId, messageId).catch(() => {});
                return true;
            }

            case 'threshold_smartMoney_1':
            case 'threshold_smartMoney_2':
            case 'threshold_smartMoney_3': {
                const level = Number(action.split('_').pop());
                this.userManager.setThreshold(chatId, 'smartMoney', level);
                this.renderThresholdMenu(chatId, messageId).catch(() => {});
                return true;
            }

            case 'toggle_subscription':
                if (this.userManager.isSubscribed(chatId)) {
                    this.userManager.unsubscribe(chatId);
                    this.showMainPanel(chatId, { messageId, flashMessage: '❌ 已停止信号订阅' }).catch(() => {});
                } else {
                    this.userManager.subscribe(chatId);
                    this.showMainPanel(chatId, { messageId, flashMessage: '✅ 已开启信号订阅' }).catch(() => {});
                }
                return true;

            case 'show_main_menu':
            case 'panel_overview':
            case 'panel_refresh':
            case 'panel_stats':
            case 'refresh_main':
            case 'refresh_stats':
            case 'show_status':
                this.showMainPanel(chatId, { messageId }).catch(() => {});
                return true;

            case 'panel_notifications':
            case 'show_settings':
            case 'notification_settings':
            case 'threshold_arbitrage':
            case 'threshold_orderbook':
                this.showMainPanel(chatId, { messageId, flashMessage: '提示：下方按钮可直接调整通知开关与阈值档位。' }).catch(() => {});
                return true;

            case 'panel_modules':
            case 'show_modules':
                this.showMainPanel(chatId, { messageId, flashMessage: '提示：当前模块状态已在面板中展示。' }).catch(() => {});
                return true;

            case 'panel_stats':
            case 'show_stats':
                this.showMainPanel(chatId, { messageId, flashMessage: '统计摘要已刷新。' }).catch(() => {});
                return true;

            case 'panel_presets':
                this.showMainPanel(chatId, { messageId, flashMessage: '提示：预设功能已整合，请使用阈值按钮调节阈值。' }).catch(() => {});
                return true;

            case 'panel_help':
            case 'show_help_info':
            case 'show_help':
                this.sendHelpMessage(chatId).catch(() => {});
                return true;

            case 'show_alerts':
                this.renderAlertPanel(chatId, messageId).catch(() => {});
                return true;

            case 'notification_arbitrage_enable':
                if (!this.userManager.isNotificationEnabled(chatId, 'arbitrage')) {
                    this.userManager.toggleNotification(chatId, 'arbitrage');
                }
                this.showMainPanel(chatId, { messageId, flashMessage: '🔔 套利通知已开启' }).catch(() => {});
                this.sendNotificationPrompt(chatId, 'arbitrage').catch(() => {});
                return true;

            case 'notification_arbitrage_disable':
                if (this.userManager.isNotificationEnabled(chatId, 'arbitrage')) {
                    this.userManager.toggleNotification(chatId, 'arbitrage');
                }
                this.showMainPanel(chatId, { messageId, flashMessage: '🔕 套利通知已关闭' }).catch(() => {});
                this.sendNotificationPrompt(chatId, 'arbitrage').catch(() => {});
                return true;

            case 'notification_orderbook_enable':
                if (!this.userManager.isNotificationEnabled(chatId, 'orderbook')) {
                    this.userManager.toggleNotification(chatId, 'orderbook');
                }
                this.showMainPanel(chatId, { messageId, flashMessage: '🔔 订单簿通知已开启' }).catch(() => {});
                this.sendNotificationPrompt(chatId, 'orderbook').catch(() => {});
                return true;

            case 'notification_orderbook_disable':
                if (this.userManager.isNotificationEnabled(chatId, 'orderbook')) {
                    this.userManager.toggleNotification(chatId, 'orderbook');
                }
                this.showMainPanel(chatId, { messageId, flashMessage: '🔕 订单簿通知已关闭' }).catch(() => {});
                this.sendNotificationPrompt(chatId, 'orderbook').catch(() => {});
                return true;

            case 'notification_closing_enable':
                if (!this.userManager.isNotificationEnabled(chatId, 'closing')) {
                    this.userManager.toggleNotification(chatId, 'closing');
                }
                this.showMainPanel(chatId, { messageId, flashMessage: '🔔 扫尾盘通知已开启' }).catch(() => {});
                this.sendNotificationPrompt(chatId, 'closing').catch(() => {});
                return true;

            case 'notification_closing_disable':
                if (this.userManager.isNotificationEnabled(chatId, 'closing')) {
                    this.userManager.toggleNotification(chatId, 'closing');
                }
                this.showMainPanel(chatId, { messageId, flashMessage: '🔕 扫尾盘通知已关闭' }).catch(() => {});
                this.sendNotificationPrompt(chatId, 'closing').catch(() => {});
                return true;

            case 'preset_conservative':
            case 'preset_balanced':
            case 'preset_aggressive':
            case 'preset_maximum':
            case 'preset_test':
            case 'preset_custom':
                this.showMainPanel(chatId, { messageId, flashMessage: '提示：预设功能已合并，请使用阈值按钮调节阈值。' }).catch(() => {});
                return true;

            case 'pause':
                if (this.userManager.isNotificationEnabled(chatId, 'arbitrage')) {
                    this.userManager.toggleNotification(chatId, 'arbitrage');
                }
                if (this.userManager.isNotificationEnabled(chatId, 'orderbook')) {
                    this.userManager.toggleNotification(chatId, 'orderbook');
                }
                if (this.userManager.isNotificationEnabled(chatId, 'closing')) {
                    this.userManager.toggleNotification(chatId, 'closing');
                }
                await this.showMainPanel(chatId, {
                    messageId,
                    flashMessage: '⏸️ 已暂停所有通知'
                });
                await this.sendNotificationPrompt(chatId, 'arbitrage');
                await this.sendNotificationPrompt(chatId, 'orderbook');
                await this.sendNotificationPrompt(chatId, 'closing');
                return true;

            case 'resume':
                if (!this.userManager.isNotificationEnabled(chatId, 'arbitrage')) {
                    this.userManager.toggleNotification(chatId, 'arbitrage');
                }
                if (!this.userManager.isNotificationEnabled(chatId, 'orderbook')) {
                    this.userManager.toggleNotification(chatId, 'orderbook');
                }
                if (!this.userManager.isNotificationEnabled(chatId, 'closing')) {
                    this.userManager.toggleNotification(chatId, 'closing');
                }
                await this.showMainPanel(chatId, {
                    messageId,
                    flashMessage: '▶️ 已恢复所有通知'
                });
                await this.sendNotificationPrompt(chatId, 'arbitrage');
                await this.sendNotificationPrompt(chatId, 'orderbook');
                await this.sendNotificationPrompt(chatId, 'closing');
                return true;

            case 'label_arbitrage':
            case 'label_orderbook':
                return true;

            case 'set_lang_en':
                this.userManager.setLang(chatId, 'en');
                this.showMainPanel(chatId, { messageId, flashMessage: '🌐 Language switched to English' }).catch(() => {});
                return true;

            case 'set_lang_zh-CN':
                this.userManager.setLang(chatId, 'zh-CN');
                this.showMainPanel(chatId, { messageId, flashMessage: '🌐 已切换为中文' }).catch(() => {});
                return true;

            case 'show_main_panel':
                this.showMainPanel(chatId, { messageId }).catch(() => {});
                return true;

            default:
                return false;
        }
    }

    /**
     * 计算成功率
     */
    calculateSuccessRate() {
        const arbStats = this.modules.arbitrage?.getStats();
        const obStats = this.modules.orderbook?.getStats();
        const closingStats = this.modules.closing?.getStats();

        const closingEmissions = closingStats?.emissions || 0;

        const totalDetected = (arbStats?.detected || 0)
            + (obStats?.detected || 0)
            + closingEmissions;

        const totalSent = (arbStats?.sent || 0)
            + (obStats?.sent || 0)
            + closingEmissions;

        if (totalDetected === 0) return 0;
        return ((totalSent / totalDetected) * 100).toFixed(1);
    }

    escapeMarkdown(text) {
        if (!text) {
            return '';
        }
        return text.replace(/([_*`\[\]\(\)])/g, '\\$1');
    }

    stripMarkdown(text) {
        if (!text) {
            return '';
        }
        return text.replace(/[*_`]/g, '');
    }

    isMarkdownParseError(description = '') {
        if (!description) {
            return false;
        }
        const lower = description.toLowerCase();
        return lower.includes("can't parse entities") || lower.includes('parse_mode');
    }

    isRateLimitError(description = '') {
        if (!description) {
            return false;
        }
        return description.toLowerCase().includes('too many requests');
    }

    getRetryAfterSeconds(error) {
        const retryFromBody = error?.response?.body?.parameters?.retry_after;
        if (retryFromBody != null) {
            const parsed = parseFloat(retryFromBody);
            if (!Number.isNaN(parsed) && parsed > 0) {
                return parsed;
            }
        }
        const retryHeader = error?.response?.headers?.['retry-after'];
        if (retryHeader != null) {
            const parsed = parseFloat(retryHeader);
            if (!Number.isNaN(parsed) && parsed > 0) {
                return parsed;
            }
        }
        return 1;
    }

    scheduleRateLimitRetry(key, delayMs, fn) {
        const safeDelay = Math.max(delayMs || 0, 1000);
        if (this.rateLimitTimers.has(key)) {
            clearTimeout(this.rateLimitTimers.get(key));
        }
        const timer = setTimeout(async () => {
            this.rateLimitTimers.delete(key);
            try {
                await fn();
            } catch (error) {
                const description = error?.response?.body?.description || error.message;
                console.error(`❌ 限频重试失败 (${key}): ${description}`);
            }
        }, safeDelay + 1000); // 追加1秒缓冲，防止再次触发限频
        this.rateLimitTimers.set(key, timer);
    }

    pruneRateLimitTimers(maxTimers = 5000) {
        if (this.rateLimitTimers.size <= maxTimers) return;
        // 这里只能简单清空最旧的键，因为 Node 的 Timeout 无法直接取创建时间
        const keys = Array.from(this.rateLimitTimers.keys());
        const excess = this.rateLimitTimers.size - maxTimers;
        for (let i = 0; i < excess; i++) {
            const key = keys[i];
            clearTimeout(this.rateLimitTimers.get(key));
            this.rateLimitTimers.delete(key);
        }
    }

    rememberReplyKeyboard(chatId, serializedLayout) {
        this.replyKeyboardState.set(chatId, { layout: serializedLayout, updatedAt: Date.now() });
        this.pruneReplyKeyboard();
        this.pruneRateLimitTimers();
    }

    pruneReplyKeyboard(maxAgeMs = 7 * 24 * 60 * 60 * 1000, maxSize = 5000) {
        const now = Date.now();
        for (const [cid, state] of this.replyKeyboardState) {
            if (!state?.updatedAt) continue;
            if (now - state.updatedAt > maxAgeMs) {
                this.replyKeyboardState.delete(cid);
            }
        }

        if (this.replyKeyboardState.size <= maxSize) return;
        const sorted = Array.from(this.replyKeyboardState.entries()).sort((a, b) => (a[1].updatedAt || 0) - (b[1].updatedAt || 0));
        const excess = sorted.length - maxSize;
        for (let i = 0; i < excess; i++) {
            this.replyKeyboardState.delete(sorted[i][0]);
        }
    }

    rememberMainPanel(chatId, messageId) {
        this.mainPanels.set(chatId, { messageId, updatedAt: Date.now() });
        this.pruneMainPanels();
        this.pruneRateLimitTimers();
        this.pruneNotificationPrompts();
    }

    pruneNotificationPrompts(maxSize = 5000) {
        if (this.notificationPromptMessages.size <= maxSize) return;
        const keys = Array.from(this.notificationPromptMessages.keys());
        const excess = keys.length - maxSize;
        for (let i = 0; i < excess; i++) {
            this.notificationPromptMessages.delete(keys[i]);
        }
    }

    pruneMainPanels(maxAgeMs = 7 * 24 * 60 * 60 * 1000, maxSize = 5000) {
        const now = Date.now();
        for (const [cid, info] of this.mainPanels) {
            if (!info?.updatedAt) continue;
            if (now - info.updatedAt > maxAgeMs) {
                this.mainPanels.delete(cid);
            }
        }

        if (this.mainPanels.size <= maxSize) return;
        const sorted = Array.from(this.mainPanels.entries()).sort((a, b) => (a[1].updatedAt || 0) - (b[1].updatedAt || 0));
        const excess = sorted.length - maxSize;
        for (let i = 0; i < excess; i++) {
            this.mainPanels.delete(sorted[i][0]);
        }
    }

    /**
     * 更新信号计数
     */
    incrementSignalCount() {
        this.botState.signalsSent++;
        this.botState.lastSignalTime = Date.now();
    }

    /**
     * 检查是否暂停
     */
    isPaused() {
        return this.botState.paused;
    }

    /**
     * /preset 命令 - 切换预设配置
     */
    async handlePreset(msg) {
        const chatId = msg.chat.id;
        await this.showMainPanel(chatId, {
            flashMessage: '提示：预设功能已整合，请使用阈值按钮调节阈值。'
        });
    }

    /**
     * /subscribe 命令 - 订阅信号
     */
    async handleSubscribe(msg) {
        const chatId = msg.chat.id;

        // 注册并订阅用户
        this.userManager.registerUser(chatId, msg.from);
        this.userManager.subscribe(chatId);
        await this.showMainPanel(chatId, {
            flashMessage: '✅ 已开启信号订阅'
        });
    }

    /**
     * /unsubscribe 命令 - 取消订阅
     */
    async handleUnsubscribe(msg) {
        const chatId = msg.chat.id;

        const success = this.userManager.unsubscribe(chatId);
        await this.showMainPanel(chatId, {
            flashMessage: success ? '❌ 已停止信号订阅' : '⚠️ 当前未订阅任何信号'
        });
    }

    /**
     * /csv 命令 - 生成CSV报告（全员可用）
     */
    async handleCsvReport(msg) {
        const chatId = msg.chat.id;

        console.log(`📊 [CSV] 收到 /csv 命令, chatId=${chatId}`);
        await this.bot.sendMessage(chatId, '📊 正在生成 CSV 报告 (滚动24小时)...\n⏳ 预计需要1-2分钟，请稍候...');
        
        try {
            const { spawnSync } = require('child_process');
            const path = require('path');
            const os = require('os');
            const fs = require('fs');
            
            const scriptPath = path.join(__dirname, '../scripts/csv-report.js');
            const defaultLogPath = path.join(__dirname, '../logs/polymarket.log');
            const logPath = process.env.CSV_LOG_FILE || defaultLogPath;
            
            // 执行脚本
            const timeoutMs = Number(process.env.CSV_REPORT_TIMEOUT_MS || 180000);
            const result = spawnSync('node', [scriptPath, logPath], {
                encoding: 'utf-8',
                timeout: Number.isFinite(timeoutMs) ? timeoutMs : 180000,
                maxBuffer: 20 * 1024 * 1024
            });
            if (result.error) {
                throw new Error(`CSV脚本执行失败: ${result.error.message}`);
            }
            if (result.status !== 0) {
                throw new Error(`CSV脚本退出码=${result.status}: ${String(result.stderr || '').trim() || '未知错误'}`);
            }
            const csv = String(result.stdout || '');
            if (!csv.trim()) {
                const stderrPreview = String(result.stderr || '').trim().split('\n').slice(0, 3).join(' | ');
                throw new Error(`CSV输出为空${stderrPreview ? `: ${stderrPreview}` : ''}`);
            }
            
            // 保存为文件
            const date = new Date().toISOString().slice(0, 10);
            const fileName = `polymarket-report-${date}.csv`;
            const filePath = path.join(os.tmpdir(), fileName);
            fs.writeFileSync(filePath, csv);
            
            // 发送文件
            await this.bot.sendDocument(chatId, filePath, {
                caption: `📊 Polymarket 24小时报告\n📅 ${new Date().toISOString().slice(0, 16)} UTC`
            });
            
            // 清理临时文件
            fs.unlinkSync(filePath);
            
        } catch (err) {
            console.error('❌ CSV报告生成失败:', err.message);
            await this.bot.sendMessage(chatId, `❌ 报告生成失败: ${err.message}`);
        }
    }

    /**
     * 套利信号阈值设置界面
     */
    async handleArbitrageThreshold(chatId) {
        await this.showMainPanel(chatId, {
            flashMessage: '提示：在下方选择合适的套利阈值档位。'
        });
    }

    /**
     * 订单簿信号阈值设置界面
     */
    async handleOrderbookThreshold(chatId) {
        await this.showMainPanel(chatId, {
            flashMessage: '提示：在下方选择合适的订单簿阈值档位。'
        });
    }

    /**
     * 通知管理界面
     */
    async handleNotificationSettings(chatId) {
        await this.showMainPanel(chatId, {
            flashMessage: '提示：下方按钮可同时调整通知开关与档位。'
        });
    }
}

module.exports = CommandHandler;
