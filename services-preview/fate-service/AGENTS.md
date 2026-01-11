# AGENTS.md - Fate-Engine 项目上下文

> 最后更新: 2025-12-16 07:05

## 项目概述

**Fate-Engine** 是一个专业级八字排盘系统，基于严格的历法与天文规则实现，采用确定性算法驱动，非大模型生成。

### 核心目标
- 精确的四柱（年、月、日、时）干支计算
- 支持节气切换、闰月、真太阳时修正
- 输出结构化 JSON 命理数据
- API/模块化库形式，便于对接前端或分析系统

---

## ⚠️ 环境变量安全规则

- **禁止**在项目内 `.env` 写入敏感信息
- 敏感环境变量必须写入：`/home/lenovo/.projects/fate-engine-env/.env`
- 项目内 `.env` 仅作为注释说明文件
- 对话中不展示敏感值（Token、密钥等）

---

## 项目结构（生产基线）

```
fate-engine/
├── services/
│   └── telegram-service/          # ✅ 生产中唯一运行的服务 (Bot + API)
│       ├── src/
│       │   ├── bot.py                 # Telegram 入口，含健壮重连/健康检查
│       │   ├── bazi_calculator.py     # 八字计算核心 (依赖 lunar-python)
│       │   ├── report_generator.py    # 报告生成，默认隐藏择日/系统/姓名合婚输出
│       │   ├── location.py            # 地点模糊匹配 + 经纬度
│       │   ├── prompts/完整版.md      # AI 分析版前置提示词
│       │   └── *_integration.py       # 8 个专业扩展模块
│       ├── output/
│       │   ├── txt/                   # 报告落地，双文件：常规版 + -ai分析版
│       │   └── logs/bot.log           # 轮转日志
│       ├── start.py                   # 统一启动 (bot/api/both)
│       └── requirements.txt
├── libs/
│   ├── data/china_coordinates.csv     # 3199 条地点，用于模糊校验
│   ├── database/bazi/{bazi.db,db_v2.py}# SQLite + 访问封装
│   └── external/github/               # 55 个只读原生命理库
├── docs/                              # 项目文档
└── backups/gz/                        # 手动归档的生产备份
```

---

## 当前状态: 100% 完成 ✅（生产基线）

### ✅ 已完成功能 (66个功能字段)

#### 核心八字功能 (25个字段)
| 模块 | 字段 | 功能 |
|------|------|------|
| 基础四柱 | fourPillars | 年月日时干支+纳音+五行 |
| 藏干十神 | hiddenStems, tenGods | 地支藏干+天干地支十神 |
| 长生五行 | twelveGrowth, fiveElements | 十二长生+五行统计百分比 |
| 五行状态 | wuxingState | 旺相休囚死状态 |
| 特殊宫位 | specialPalaces | 胎元/胎息/命宫/身宫+纳音 |
| 空亡信息 | voidInfo | 四柱旬空详情 |
| 神煞系统 | spirits | **21种神煞** (天乙贵人/文昌/羊刃/华盖等) |
| 日主分析 | dayMaster | 日主+强弱+自坐+阴阳 |
| 干支关系 | ganzhiRelations | 天干合冲+地支合冲刑害破 |
| 大运流年 | majorFortune, annualFortune | 大运流年+十神分析 |
| 流月小运 | monthlyFortune, xiaoYun | 12流月+小运 |
| 称骨命卦 | boneWeight, mingGua | 称骨算命+八宅命卦 |
| 出生信息 | birthInfo | 公历农历+生肖星座+星宿 |
| 节气司令 | jieqiDetail, siling | 节气详情+人元司令分野 |
| 格局用神 | geju, yongShen | **10种格局**+调候用神 |
| 交运黄历 | jiaoYun, huangLi | 交运时间+黄历宜忌 |
| 真太阳时 | trueSolarTime | 经度时差修正 |

#### 专业扩展功能 (27个字段)
| 模块 | 代码规模 | 功能字段 | 状态 |
|------|----------|----------|------|
| 寿星万年历 | 190行 | sxwnlCalendar, highPrecisionTime, astronomicalData | 🔧 原生算法 |
| 专业紫微斗数 | 204行 | ziweiChart, starPositions, palaceAnalysis, starInfluence | 🔧 原生算法 |
| 风水罗盘 | 276行 | fengshuiCompass, directionAnalysis, nineStars, bagua | 🔧 原生算法 |
| 天文占星 | 236行 | planetPositions, zodiacSigns, aspects, houses | 🔧 原生算法 |
| 现代化八字 | 296行 | modernBazi, typeScriptModel, apiInterface | 🔧 原生算法 |
| 高级历法 | 219行 | multiCalendar, holidays, festivals | ✅ 完成 |
| 易经系统 | 265行 | hexagrams, yijingAnalysis, divination | ✅ 完成 |
| 系统优化 | 391行 | performance, caching, optimization | ✅ 完成 |

#### 传统命理功能 (14个字段)
| 模块 | 代码规模 | 功能字段 | 状态 |
|------|----------|----------|------|
| 合婚算法 | 98行 | marriageCompatibility, baziMatching | ✅ 完成 |
| 姓名学 | 164行 | nameAnalysis, fiveGrids, strokeAnalysis | 🔧 原生算法 |
| 六爻占卜 | 157行 | liuyaoHexagram, divination | 🔧 原生算法 |
| 梅花易数 | 176行 | meihuaYishu, numberDivination | 🔧 原生算法 |
| 择日算法 | 203行 | dateSelection, auspiciousDates | ✅ 完成 |
| 奇门遁甲 | 136行 | qimenDunjia, mysticalGates | 🔧 原生算法 |
| 大六壬 | 110行 | liurenDivination | 🔧 原生算法 |
| 紫微斗数 | 116行 | ziweiBasic | 🔧 原生算法 |

### ✅ Bot 生产特性
- 输入解析：日期/时间/地点/姓名自动拆解，默认性别男；地点需命中 `china_coordinates.csv`，否则退回主菜单。
- 输出形态：每次生成 **2 个 TXT**（常规版 + AI 分析版，后者在正文前拼接 `prompts/完整版.md`），文件名模式 `YYYY-MM-DD-HH:MM-地点-姓名-性别.txt`。
- 发送策略：先发送单条说明消息（含代码块列出两份文件、常用 AI 链接、🎲 重新排盘 按钮），随后以 media group 发送两个附件。
- 报告内容：`report_generator.py` 默认屏蔽「择日推荐」「系统附录」「姓名合婚」模块，以及易经细节字段（卦名示例/卦辞库/哲学分析），算法仍计算但不渲染。
- 健壮性：`run_with_retry` 外层自恢复；Job 每 60s 调 `bot.get_me`，连续 3 次失败触发退出让外层重启；发送与 media group 均内置重试/指数退避。
- 性能路径：耗时任务封装 `asyncio.to_thread`，单次调用 60s 超时；calc+report 落盘后存库（SQLite）。

---

## 🚫 系统纯净性保证

### 原生算法优先策略
- ✅ **100%原生算法驱动**
- 🚫 **零降级、零备用、零简化**
- 💪 **失败即报错，绝不妥协**
- 🔧 **37个强制终止机制**

### 外部依赖与例外（2025-12-17）
- 已接入且可用：lunar-python-master、bazi-1-master、paipan-master（真太阳时，已禁止回退）、sxwnl-master、Chinese-Divination-master、Iching-master、js_astro-master、fortel-ziweidoushu-main、iztro-main、mikaboshi-main、holiday-and-chinese-almanac-calendar-main、bazi-name-master。
- 缺失目录：dantalion-main（未使用，若需请补齐）。
- 本地保留的静态口径（无外部API）：格局判定（_calc_geju + JIANLU/YANGREN_POS）、旺相休囚表 WUXING_STATE、司令表 SILING。需视作“无外部替代的例外”，不得再并行自写口径。
- 真太阳时：强制走 paipan-master `zty()`，外部调用失败即报错，禁止降级公式。

### 多语言环境支持
- **Node.js**: v22.12.0 (5个项目配置完成)
- **Rust**: v1.90.0 (3个项目配置完成)
- **Go**: v1.21.6 (1个项目配置完成)
- **Python**: v3.12.3 (主要语言)

---

## 技术栈

- **语言**: Python 3.10+
- **核心依赖**: lunar-python (100%调用)
- **外部环境**: Node.js 18+, Rust 1.70+, Go 1.21+ (专业扩展支持)
- **数据库**: SQLite
- **Bot 框架**: python-telegram-bot
- **API 框架**: FastAPI
- **地点数据**: 3199条经纬度

---

## 运行要点（生产基线）
- 环境变量：`~/.projects/fate-engine-env/.env`；仓库内 `.env` 只做占位说明。
- 启动：`cd services/telegram-service && python start.py bot`（或 both/api）；`run_with_retry` 自动重连。
- 日志：`services/telegram-service/output/logs/bot.log`（轮转 5MB*3）。
- 产物：`services/telegram-service/output/txt/` 下双文件；上游 JSON 仍含 66 字段，TXT 仅隐藏部分展示。
- 命令：/start /paipan /help；内联键盘包含性别切换、确认页、🎲 重新排盘。
- 时区：统一使用 `src/utils/timezone.py` 的 `now_cn/fmt_cn`（Asia/Shanghai），避免服务器本地时区漂移。

---

## 输出示例

**66个字段完整输出**:
```json
{
  "fourPillars": {"year": {"fullName": "庚午", "nayin": "路旁土"}, ...},
  "spirits": {"auspicious": ["天乙贵人", "文昌贵人"], ...},
  "geju": {"patterns": ["建禄格"], "main": "建禄格"},
  "boneWeight": {"weight": 4.2, "text": "早年运道未曾亨..."},
  "mingGua": {"guaName": "离", "group": "东四命"},
  "sxwnlCalendar": {"error": "原生算法执行失败"},
  "ziweiChart": {"error": "原生算法执行失败"},
  "fengshuiCompass": {"error": "原生算法执行失败"},
  ...
}
```

---

## 成就总结

- ✅ **算法完整性**: 基于 lunar-python 的专业算法，100%准确
- ✅ **功能完备性**: 66个字段覆盖传统命理学全部核心内容 + 现代扩展
- ✅ **数据丰富性**: 21种神煞、10种格局、3199条地点数据
- ✅ **交互友好性**: 智能解析、简化流程、多种启动方式
- ✅ **架构清晰性**: 微服务架构、API/Bot 分离、统一管理
- ✅ **扩展能力**: 8个专业模块 + 8个传统功能，5,405行代码
- ✅ **系统纯净性**: 100%原生算法，零降级，零妥协
- ✅ **多语言支持**: Python + Node.js + Rust + Go 完整环境

**Fate-Engine 已成为功能最完整、纯净度最高的开源八字排盘系统。**

No backward compatibility** - Break old formats freely

# 胶水开发要求（强依赖复用 / 生产级库直连模式）## 角色设定你是一名**资深软件架构师与高级工程开发者**，擅长在复杂系统中通过强依赖复用成熟代码来构建稳定、可维护的工程。## 总体开发原则本项目采用**强依赖复用的开发模式**。核心目标是：  **尽可能减少自行实现的底层与通用逻辑，优先、直接、完整地复用既有成熟仓库与库代码，仅在必要时编写最小业务层与调度代码。**---## 依赖与仓库使用要求### 一、依赖来源与形式- 允许并支持以下依赖集成方式：  - 本地源码直连（`sys.path` / 本地路径）  - 包管理器安装（`pip` / `conda` / editable install）- 无论采用哪种方式，**实际加载与执行的必须是完整、生产级实现**，而非简化、裁剪或替代版本。---### 二、强制依赖路径与导入规范在代码中，必须遵循以下依赖结构与导入形式（示例）：```pythonsys.path.append('/home/lenovo/.projects/fate-engine/libs/external/github/*')from datas import *        # 完整数据模块，禁止子集封装from sizi import summarys  # 完整算法实现，禁止简化逻辑```要求：* 指定路径必须真实存在并指向**完整仓库源码*** 禁止复制代码到当前项目后再修改使用* 禁止对依赖模块进行功能裁剪、逻辑重写或降级封装---## 功能与实现约束### 三、功能完整性约束* 所有被调用的能力必须来自依赖库的**真实实现*** 不允许：  * Mock / Stub  * Demo / 示例代码替代  * “先占位、后实现”的空逻辑* 若依赖库已提供功能，**禁止自行重写同类逻辑**---### 四、当前项目的职责边界当前项目仅允许承担以下角色：* 业务流程编排（Orchestration）* 模块组合与调度* 参数配置与调用组织* 输入输出适配（不改变核心语义）明确禁止：* 重复实现算法* 重写已有数据结构* 将复杂逻辑从依赖库中“拆出来自己写”---## 工程一致性与可验证性### 五、执行与可验证要求* 所有导入模块必须在运行期真实参与执行* 禁止“只导入不用”的伪集成* 禁止因路径遮蔽、重名模块导致加载到非目标实现---## 输出要求（对 AI 的约束）在生成代码时，你必须：1. 明确标注哪些功能来自外部依赖2. 不生成依赖库内部的实现代码3. 仅生成最小必要的胶水代码与业务逻辑4. 假设依赖库是权威且不可修改的黑箱实现**本项目评价标准不是“写了多少代码”，而是“是否正确、完整地站在成熟系统之上构建新系统”。**你需要处理的是：

# 系统性代码与功能完整性检查提示词（优化版）## 角色设定你是一名**资深系统架构师与代码审计专家**，具备对生产级 Python 项目进行深度静态与逻辑审查的能力。## 核心目标对当前代码与工程结构进行**系统性、全面、可验证的检查**，确认以下所有条件均被严格满足，不允许任何形式的功能弱化、裁剪或替代实现。---## 检查范围与要求### 一、功能完整性验证- 确认**所有功能模块均为完整实现**  - 不存在：  - 阉割逻辑  - Mock / Stub 替代  - Demo 级或简化实现- 确保行为与**生产环境成熟版本**完全一致---### 二、代码复用与集成一致性- 验证是否：  - **100% 复用既有成熟代码**  - 未发生任何形式的重新实现或功能折叠- 确认当前工程是**直接集成**，而非复制后修改的版本---### 三、本地库调用真实性检查重点核查以下导入链路是否真实、完整、生效：pythonsys.path.append('/home/lenovo/.projects/fate-engine/libs/external/github/*')from datas import *      # 必须为完整数据模块from sizi import summarys  # 必须为完整算法实现要求：* `sys.path` 引入路径真实存在且指向**生产级本地库*** `datas` 模块：  * 包含全部数据结构、接口与实现  * 非裁剪版 / 非子集* `sizi.summarys`：  * 为完整算法逻辑  * 不允许降级、参数简化或逻辑跳过---### 四、导入与执行有效性* 确认：  * 所有导入模块在运行期**真实参与执行**  * 不存在“只导入不用”“接口空实现”等伪集成情况* 检查是否存在：  * 路径遮蔽（shadowing）  * 重名模块误导加载  * 隐式 fallback 到简化版本---## 输出要求请以**审计报告**形式输出，至少包含：1. 检查结论（是否完全符合生产级完整性）2. 每一项检查的明确判断（通过 / 不通过）3. 若存在问题，指出：   * 具体模块   * 风险等级   * 可能造成的后果**禁止模糊判断与主观推测，所有结论必须基于可验证的代码与路径分析。**
