/**
 * 💰 价格套利检测器
 *
 * 功能：检测 YES + NO 价格是否偏离 1.0
 * 难度：⭐☆☆☆☆
 */

class ArbitrageDetector {
    constructor(config = {}) {
        // 配置
        this.MIN_PROFIT = config.minProfit || 0.03;  // 最低3%净利润
        this.TRADING_FEE = config.tradingFee || 0.02; // 2%交易费用
        this.SLIPPAGE = config.slippage || 0.005;     // 0.5%滑点
        this.MIN_DEPTH = config.minDepth || 100;      // 最小深度$100
        this.MAX_PRICE_AGE_MS = config.maxPriceAge || 60000;  // 价格最大有效期60秒
        this.MAX_PRICE_TIME_DIFF_MS = config.maxPriceTimeDiff || 30000;  // YES/NO最大时间差30秒
        this.COOLDOWN = config.cooldown || 60000;     // 1分钟冷却
        this.MAX_SIGNALS_PER_HOUR = config.maxSignalsPerHour || 10;

        // 价格缓存（存在内存里）
        this.priceCache = new Map();
        this.marketTokenIndex = new Map();

        // 市场元数据缓存（存储market name等信息）
        this.marketMetadata = new Map();

        this.debug = Boolean(config.debug);

        // 冷却时间管理
        this.lastSignals = new Map();

        // 警报历史（最多10条）
        this.alertHistory = [];
        this.MAX_ALERT_HISTORY = 10;

        // 每小时信号计数
        this.hourlySignals = [];

        // 统计
        this.stats = {
            detected: 0,      // 检测到的机会数
            sent: 0,          // 发送的信号数
            skipped: 0,       // 跳过的信号数（冷却/限流）
            lastSignal: null  // 上次信号时间
        };

        console.log('✅ 套利检测器初始化完成');
        console.log(`   最低净利润: ${(this.MIN_PROFIT * 100).toFixed(1)}%`);
        console.log(`   交易费用: ${(this.TRADING_FEE * 100).toFixed(1)}%`);
        console.log(`   滑点: ${(this.SLIPPAGE * 100).toFixed(1)}%`);
        console.log(`   最小深度: $${this.MIN_DEPTH}`);
    }

    /**
     * 处理价格更新消息
     * @param {Object} message - WebSocket消息
     * @returns {Object|null} - 套利信号对象或null
     */
    processPrice(message) {
        try {
            const payload = message.payload;

            // 提取关键信息 - 支持activity.trades和clob_market.price_change两种格式
            const tokenId = payload.asset || payload.token_id || payload.tokenId;

            // 改进：优先使用ask价格，而非成交价
            let price = null;

            // 1. 尝试从price_change消息提取best_ask
            if (payload.pc && Array.isArray(payload.pc) && payload.pc.length > 0) {
                const priceChange = payload.pc[0];
                if (priceChange.ba) {
                    price = parseFloat(priceChange.ba);  // 使用best_ask
                    if (this.debug) {
                        console.debug(`📊 使用best_ask价格: ${price}`);
                    }
                }
            }

            // 2. 尝试从orderbook消息提取asks[0]
            if (!price && payload.asks && Array.isArray(payload.asks) && payload.asks.length > 0) {
                price = parseFloat(payload.asks[0].price);
                if (this.debug) {
                    console.debug(`📊 使用orderbook ask价格: ${price}`);
                }
            }

            // 3. 降级到原有的price字段（成交价/last）
            if (!price && payload.price !== undefined) {
                price = parseFloat(payload.price);
                if (this.debug) {
                    console.debug(`⚠️ 降级使用成交价: ${price} (无ask数据)`);
                }
            }

            const market = payload.conditionId || payload.condition_id || payload.market;

            if (!tokenId || !price || isNaN(price) || !market) {
                return null;
            }

            // 存入缓存
            this.updateCache(tokenId, price, market, payload);

            // 尝试检测套利
            const opportunity = this.detect(market, tokenId);

            return opportunity;

        } catch (error) {
            console.error('❌ 处理价格更新失败:', error.message);
            return null;
        }
    }

    /**
     * 处理clob_market的price_change消息
     * 专门处理包含ask/bid数据的消息
     */
    processPriceChange(message) {
        try {
            const payload = message.payload;

            // price_change消息格式：{ m: market, pc: [{ a: asset_id, ba: best_ask, bb: best_bid, ... }] }
            if (!payload.pc || !Array.isArray(payload.pc)) {
                return null;
            }

            const market = payload.m;
            let opportunities = [];

            // 处理每个价格变化
            payload.pc.forEach(priceChange => {
                const tokenId = priceChange.a;  // asset_id
                const bestAsk = priceChange.ba ? parseFloat(priceChange.ba) : null;
                const bestBid = priceChange.bb ? parseFloat(priceChange.bb) : null;

                if (tokenId && bestAsk && !isNaN(bestAsk)) {
                    // 构建兼容的payload格式
                    const compatPayload = {
                        ...payload,
                        // outcome 必须来自 token 元信息（Yes/No），不能从 side(BUY/SELL) 推断
                        outcome: priceChange.o || priceChange.outcome || payload?.outcome || null,
                        tokenId: tokenId
                    };

                    // 使用ask价格更新缓存
                    this.updateCache(tokenId, bestAsk, market, compatPayload);

                    // 尝试检测套利
                    const opportunity = this.detect(market, tokenId);
                    if (opportunity) {
                        opportunities.push(opportunity);
                    }
                }
            });

            // 返回第一个发现的套利机会
            return opportunities.length > 0 ? opportunities[0] : null;

        } catch (error) {
            console.error('❌ 处理price_change失败:', error.message);
            return null;
        }
    }

    /**
     * 处理clob_market的orderbook消息
     */
    processOrderbook(message) {
        try {
            const payload = message.payload;

            const market = payload.market;
            const tokenId = payload.asset_id;

            // 提取最优ask价格和深度
            if (payload.asks && payload.asks.length > 0) {
                const bestAsk = parseFloat(payload.asks[0].price);

                // 计算前3档ask深度（美元面值）
                let askDepthUsd = 0;
                const depthLevels = Math.min(3, payload.asks.length);
                for (let i = 0; i < depthLevels; i++) {
                    const size = parseFloat(payload.asks[i].size) || 0;
                    askDepthUsd += size;
                }

                if (!isNaN(bestAsk)) {
                    // 更新缓存（带深度信息）
                    this.updateCache(tokenId, bestAsk, market, payload, askDepthUsd);

                    // 尝试检测套利
                    return this.detect(market, tokenId);
                }
            }

            return null;

        } catch (error) {
            console.error('❌ 处理orderbook失败:', error.message);
            return null;
        }
    }

    /**
     * 更新价格缓存
     * @param {string} tokenId - Token ID
     * @param {number} price - 价格
     * @param {string} market - 市场ID
     * @param {object} payload - 原始消息
     * @param {number} askDepthUsd - ask深度（美元），可选
     */
    updateCache(tokenId, price, market, payload, askDepthUsd = null) {
        // 提取市场slug（优先使用eventSlug，因为它用于构建URL）
        const marketSlug = payload.eventSlug || payload.slug || null;
        const marketName = payload.title || null;
        const outcome = this.normalizeOutcome(payload.outcome || payload?.token?.outcome || payload.side);

        // 获取现有缓存，保留之前的深度数据（如果本次没有新数据）
        const existingEntry = this.priceCache.get(tokenId);
        const finalAskDepthUsd = askDepthUsd !== null ? askDepthUsd : (existingEntry?.askDepthUsd || 0);

        const cacheEntry = {
            price: price,
            market: market,
            timestamp: Date.now(),
            outcome: outcome,
            slug: marketSlug,
            eventSlug: payload.eventSlug || null,
            marketSlug: payload.slug || null,
            title: marketName,
            askDepthUsd: finalAskDepthUsd,  // 新增：ask深度
            source: askDepthUsd !== null ? 'orderbook' : (payload.pc ? 'price_change' : 'trade')  // 新增：数据来源
        };

        this.priceCache.set(tokenId, cacheEntry);
        this.updateMarketIndex(market, tokenId, outcome);

        if (this.debug) {
            const outcomeLabel = outcome || 'UNKNOWN';
            if (marketSlug) {
                console.debug(`📝 缓存价格: ${market.substring(0, 12)}... ${outcomeLabel} = ${price.toFixed(4)} [slug: ${marketSlug.substring(0, 30)}...] 深度=$${finalAskDepthUsd.toFixed(0)}`);
            } else {
                console.debug(`📝 缓存价格: ${market.substring(0, 12)}... ${outcomeLabel} = ${price.toFixed(4)} [无 slug] 深度=$${finalAskDepthUsd.toFixed(0)}`);
            }
        }
    }

    normalizeOutcome(outcome) {
        if (outcome === undefined || outcome === null) {
            return null;
        }

        const value = String(outcome).trim().toLowerCase();

        if (value === 'yes' || value === '1' || value === 'true' || value === 'buy') {
            return 'yes';
        }

        if (value === 'no' || value === '0' || value === 'false' || value === 'sell') {
            return 'no';
        }

        return null;
    }

    updateMarketIndex(market, tokenId, outcome) {
        if (!outcome) {
            return;
        }

        if (!this.marketTokenIndex.has(market)) {
            this.marketTokenIndex.set(market, { yes: null, no: null });
        }

        const entry = this.marketTokenIndex.get(market);

        if (outcome === 'yes') {
            entry.yes = tokenId;
        } else if (outcome === 'no') {
            entry.no = tokenId;
        }
    }

    /**
     * 检测套利机会（核心逻辑）
     */
    detect(market, triggerTokenId) {
        // 1. 找到这个市场的 YES 和 NO token
        const tokens = this.findMarketTokens(market);

        if (!tokens.yes || !tokens.no) {
            // 还没有完整的价格数据
            return null;
        }

        // 2. 获取价格数据
        const yesData = this.priceCache.get(tokens.yes);
        const noData = this.priceCache.get(tokens.no);

        if (!yesData || !noData) {
            return null;
        }

        // 3. 检查价格数据是否过期
        const now = Date.now();
        const yesAge = now - yesData.timestamp;
        const noAge = now - noData.timestamp;

        if (yesAge > this.MAX_PRICE_AGE_MS || noAge > this.MAX_PRICE_AGE_MS) {
            if (this.debug) {
                console.debug(`⏰ 价格数据过期: YES=${(yesAge/1000).toFixed(1)}s, NO=${(noAge/1000).toFixed(1)}s`);
            }
            return null;
        }

        // 4. 检查 YES/NO 时间差是否过大
        const timeDiff = Math.abs(yesData.timestamp - noData.timestamp);
        if (timeDiff > this.MAX_PRICE_TIME_DIFF_MS) {
            if (this.debug) {
                console.debug(`⏰ YES/NO价格不同步: 时间差=${(timeDiff/1000).toFixed(1)}s`);
            }
            return null;
        }

        // 5. 检查深度是否足够
        const yesDepth = yesData.askDepthUsd || 0;
        const noDepth = noData.askDepthUsd || 0;
        const minDepth = Math.min(yesDepth, noDepth);

        if (minDepth < this.MIN_DEPTH) {
            if (this.debug) {
                console.debug(`📉 深度不足: YES=$${yesDepth.toFixed(0)}, NO=$${noDepth.toFixed(0)}, 需要>=$${this.MIN_DEPTH}`);
            }
            return null;
        }

        const yesPrice = yesData.price;
        const noPrice = noData.price;

        // 6. 计算套利空间
        const sum = yesPrice + noPrice;

        // 只检测 sum < 1.0 的情况（买入套利）
        if (sum >= 1.0) {
            return null;
        }

        // 7. 计算利润（修复：双边手续费+滑点按成交额比例扣减）
        const grossProfit = 1.0 - sum;  // 毛利润
        const totalFee = sum * this.TRADING_FEE * 2;  // 双边手续费
        const totalSlippage = sum * this.SLIPPAGE * 2;  // 双边滑点
        const netProfit = grossProfit - totalFee - totalSlippage;  // 净利润
        const profitPercent = (netProfit / sum) * 100;  // 百分比

        // 8. 判断是否值得
        if (netProfit < this.MIN_PROFIT) {
            return null;
        }

        // 9. 检查冷却时间
        if (!this.checkCooldown(market)) {
            this.stats.skipped++;
            return null;
        }

        // 10. 检查每小时限流
        if (!this.checkHourlyLimit()) {
            this.stats.skipped++;
            return null;
        }

        // 套利机会！
        this.stats.detected++;
        this.stats.sent++;
        this.stats.lastSignal = Date.now();

        // 从缓存中获取市场名称和slug（yesData和noData在上面已声明）
        const marketName = yesData?.title || noData?.title || null;
        const marketSlug = yesData?.slug || noData?.slug || null;
        const eventSlug = yesData?.eventSlug || noData?.eventSlug || null;

        // 更新冷却时间
        this.lastSignals.set(market, Date.now());

        // 添加到警报历史
        this.alertHistory.push({
            market,
            name: marketName || market.substring(0, 12),
            time: Date.now(),
            value: profitPercent.toFixed(1) + '%',
            slug: marketSlug,
            eventSlug
        });
        if (this.alertHistory.length > this.MAX_ALERT_HISTORY) {
            this.alertHistory.shift();
        }

        // 添加到每小时计数
        this.hourlySignals.push(Date.now());

        console.log(`🎉 发现套利机会！市场: ${marketName || market.substring(0, 12)}, 净利润: ${profitPercent.toFixed(2)}%, 深度: YES=$${yesDepth.toFixed(0)} NO=$${noDepth.toFixed(0)}`);

        return {
            type: 'ARBITRAGE',
            market: market,
            marketName: marketName,  // 如果为null，formatter会显示'未知市场'
            marketSlug: marketSlug,  // 从 WebSocket payload 中提取的 slug
            eventSlug: eventSlug,
            yesPrice: yesPrice,
            noPrice: noPrice,
            sum: sum,
            cost: sum,
            payout: 1.0,
            grossProfit: grossProfit,
            grossProfitPercent: (grossProfit / sum * 100).toFixed(2),
            netProfit: netProfit,
            netProfitPercent: profitPercent.toFixed(2),
            tradingFee: this.TRADING_FEE,
            tradingFeePercent: (this.TRADING_FEE * 100 * 2).toFixed(2),  // 双边
            slippage: this.SLIPPAGE,
            slippagePercent: (this.SLIPPAGE * 100 * 2).toFixed(2),  // 双边
            totalFee: totalFee,
            totalSlippage: totalSlippage,
            yesDepth: yesDepth,  // 新增
            noDepth: noDepth,    // 新增
            minDepth: minDepth,  // 新增
            strength: 5,  // 套利信号总是5星
            urgency: 'HIGH',
            timeWindow: 600,  // 10分钟
            timestamp: Date.now(),

            // Token IDs for trading
            yesTokenId: tokens.yes,
            noTokenId: tokens.no,

            // 操作说明
            actions: [
                `买入 YES @ $${yesPrice.toFixed(3)}`,
                `买入 NO @ $${noPrice.toFixed(3)}`,
                `总成本 $${sum.toFixed(3)}`,
                `手续费 $${totalFee.toFixed(4)} (${(this.TRADING_FEE * 100 * 2).toFixed(1)}%)`,
                `滑点 $${totalSlippage.toFixed(4)} (${(this.SLIPPAGE * 100 * 2).toFixed(1)}%)`,
                `净收益 $${netProfit.toFixed(4)}`
            ]
        };
    }

    /**
     * 查找市场的 YES 和 NO token
     */
    findMarketTokens(market) {
        let entry = this.marketTokenIndex.get(market);

        if (!entry) {
            entry = { yes: null, no: null };
            this.marketTokenIndex.set(market, entry);
        }

        if (entry.yes && entry.no) {
            return entry;
        }

        for (const [tokenId, data] of this.priceCache.entries()) {
            if (data.market !== market || !data.outcome) {
                continue;
            }

            if (!entry.yes && data.outcome === 'yes') {
                entry.yes = tokenId;
            } else if (!entry.no && data.outcome === 'no') {
                entry.no = tokenId;
            }

            if (entry.yes && entry.no) {
                break;
            }
        }

        return entry;
    }

    /**
     * 检查冷却时间
     */
    checkCooldown(market) {
        const lastTime = this.lastSignals.get(market);
        if (!lastTime) return true;

        const elapsed = Date.now() - lastTime;
        if (elapsed < this.COOLDOWN) {
            console.log(`⏸️ 冷却中... 剩余 ${Math.ceil((this.COOLDOWN - elapsed) / 1000)}s`);
            return false;
        }

        return true;
    }

    /**
     * 检查每小时限流
     */
    checkHourlyLimit() {
        const now = Date.now();
        const oneHourAgo = now - 3600000;

        // 清理1小时前的记录
        this.hourlySignals = this.hourlySignals.filter(time => time > oneHourAgo);

        if (this.hourlySignals.length >= this.MAX_SIGNALS_PER_HOUR) {
            console.log(`⏸️ 达到每小时限制 (${this.MAX_SIGNALS_PER_HOUR})`);
            return false;
        }

        return true;
    }

    /**
     * 更新市场元数据
     * @param {string} market - 市场ID
     * @param {Object} metadata - 元数据 {name, description, etc}
     */
    updateMarketMetadata(market, metadata) {
        this.marketMetadata.set(market, {
            ...metadata,
            updated: Date.now()
        });
    }

    /**
     * 获取市场名称
     */
    getMarketName(market) {
        const metadata = this.marketMetadata.get(market);
        return metadata ? metadata.name : null;
    }

    /**
     * 获取统计信息
     */
    getStats() {
        return {
            ...this.stats,
            cacheSize: this.priceCache.size,
            marketsTracked: this.marketMetadata.size,
            signalsThisHour: this.hourlySignals.length
        };
    }

    /**
     * 获取警报历史
     */
    getAlertHistory() {
        return this.alertHistory.map(a => ({ ...a, type: 'arbitrage', icon: '💼' }));
    }

    /**
     * 清理过期缓存（可选）
     */
    cleanupCache(maxAge = 3600000) {
        const now = Date.now();
        let removed = 0;

        for (const [tokenId, data] of this.priceCache.entries()) {
            if (now - data.timestamp > maxAge) {
                this.priceCache.delete(tokenId);
                removed++;

                const entry = this.marketTokenIndex.get(data.market);
                if (entry) {
                    if (entry.yes === tokenId) {
                        entry.yes = null;
                    }
                    if (entry.no === tokenId) {
                        entry.no = null;
                    }
                    if (!entry.yes && !entry.no) {
                        this.marketTokenIndex.delete(data.market);
                        this.marketMetadata.delete(data.market);
                    }
                }
            }
        }

        // 清理孤立的 marketMetadata
        if (this.marketMetadata.size > 10000) {
            const validMarkets = new Set(this.marketTokenIndex.keys());
            for (const market of this.marketMetadata.keys()) {
                if (!validMarkets.has(market)) {
                    this.marketMetadata.delete(market);
                }
            }
        }

        if (removed > 0) {
            console.log(`🧹 清理了 ${removed} 条过期价格数据`);
        }
    }
}

module.exports = ArbitrageDetector;
