# 闲鱼"一人公司"多智能体系统

一个由 AI 智能体组成、模拟公司组织架构的自动化系统，在闲鱼平台上完成找货源、比价格、上架、客服、对账等全流程运营。

## 系统架构

```
┌──────────────────────────────────────────────────────────┐
│              智能体编排层 (OnePersonCompany)                │
│                                                          │
│  选品上架流水线:                                            │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐       │
│  │ 总裁  │→│ 采购  │→│ 比价  │→│审核一审│→│审核复核│→包装上架│
│  │策略  │  │找货  │  │算利润 │  │合规  │  │经营  │  发布   │
│  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘       │
│                                                          │
│  全天候保障:                                               │
│  ┌──────────┐  ┌────────┐  ┌────────┐                    │
│  │ 运营客服  │  │  会计  │  │  风控  │                    │
│  │议价发货  │  │记账对账 │  │安全    │                    │
│  └──────────┘  └────────┘  └────────┘                    │
├──────────────────────────────────────────────────────────┤
│                大模型层 (LLMClient)                        │
│  DeepSeek V3.1(主力) / R1(推理) / Qwen-Turbo(采购) /      │
│  Qwen-Plus(包装)                                          │
├──────────────────────────────────────────────────────────┤
│              闲鱼自动化层 (Tools)                           │
│  Playwright网页客户端 / WebSocket消息监听 /                │
│  拼多多+1688货源搜索                                       │
├──────────────────────────────────────────────────────────┤
│                数据层 (SQLite)                             │
│  消息 / 订单 / 交易 / 风控日志 / 统计 / Agent日志           │
└──────────────────────────────────────────────────────────┘
```

## 9 个智能体

| 角色 | 功能 | 模型 | 状态 |
|------|------|------|------|
| 总裁 Agent | 制定经营策略、审阅日报、风控决策 | DeepSeek V3 | 完成 |
| 采购 Agent | 拼多多/1688并发搜索货源，LLM筛选优质候选 | Qwen-Turbo | 完成 |
| 比价 Agent | 核算利润（运费+手续费），心理价位定价，竞争度评分 | DeepSeek V3 | 完成 |
| 审核 Agent | 一审合规检查（违禁品/违规词），二审经营复核（定价/重复/毛利） | DeepSeek R1/V3 | 完成 |
| 包装上架 Agent | 爆款标题生成，图片策略，描述模板，自动发布 | Qwen-Plus | 完成 |
| 运营 Agent | 7x24智能客服、意图分类、阶梯议价、发货协调 | DeepSeek V3 | 完成 |
| 会计 Agent | 自动记账、担保交易追踪、资金周转监控、日报 | DeepSeek V3 | 完成 |
| 风控 Agent | 频率控制、异常检测、自动暂停、健康检查 | DeepSeek V3 | 完成 |

## 选品上架流水线

核心业务流程，每 4 小时自动执行一次，也可手动触发：

```
总裁(策略关键词)
    │
    ▼
采购Agent ──→ 拼多多+1688并发搜索 → LLM筛选优质货源 → 候选清单
    │
    ▼
比价Agent ──→ 计算利润(运费+手续费) → 心理价位定价 → 竞争度评分 → 可上架清单
    │
    ▼
审核Agent(一审) ──→ 违禁品检查 → 违规词扫描 → 图片侵权 → 描述真实性 → pass/reject/modify
    │
    ▼
审核Agent(复核) ──→ 定价偏离检查 → 重复上架检查 → 毛利核验 → approve/reject
    │
    ▼
包装上架Agent ──→ 爆款标题生成 → 图片拍摄策略 → 描述模板 → 发布到闲鱼
```

## 快速开始

### 1. 安装依赖

```bash
cd xianyu-company
pip install -r requirements.txt --break-system-packages
playwright install chromium
```

### 2. 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入你的 API Key
# DEEPSEEK_API_KEY=sk-xxx  (DeepSeek注册即送额度)
# QWEN_API_KEY=sk-xxx      (通义千问，可选)

# 复制配置文件
cp config/settings.yaml config/settings.local.yaml
# 编辑 settings.local.yaml 填入真实凭据
```

### 3. 闲鱼登录（可选，模拟模式不需要）

```bash
# 方式一：交互式登录（打开浏览器扫码）
python main.py --login

# 方式二：使用Cookie导出工具（三种方法）
python tools/export_cookie.py --method playwright    # Playwright交互登录
python tools/export_cookie.py --method console       # 浏览器控制台脚本
python tools/export_cookie.py --method extension     # Chrome扩展指南
```

### 4. 启动系统

```bash
# 模拟模式（测试，不需要闲鱼Cookie和API Key也能跑）
python main.py --mock

# 生产模式（需要配置Cookie和API Key）
python main.py
```

### 5. 功能命令

```bash
# 选品上架流水线（手动执行一次找货→上架全链路）
python main.py --pipeline              # 使用上次策略关键词
python main.py --pipeline "厨房收纳盒"  # 指定关键词

# 运营管理
python main.py --report    # 生成今日日报
python main.py --health    # 检查账号健康状况
python main.py --stats     # 查看智能体操作统计
```

## 项目结构

```
xianyu-company/
├── main.py                      # 主入口
├── requirements.txt             # Python依赖
├── config/
│   ├── settings.yaml            # 全局配置模板
│   ├── settings.local.yaml      # 本地配置(不入库)
│   └── prompts/                 # Agent提示词
│       ├── ceo.md               # 总裁
│       ├── purchasing.md        # 采购
│       ├── pricing.md           # 比价
│       ├── review.md            # 审核(一审)
│       ├── review_secondary.md  # 审核(复核)
│       ├── packaging.md         # 包装上架
│       ├── operations.md        # 运营
│       ├── accounting.md        # 会计
│       └── risk_control.md      # 风控
├── src/
│   ├── config.py                # 配置加载器
│   ├── company.py               # 公司编排器(9智能体协同)
│   ├── agents/                  # 智能体定义
│   │   ├── base.py              # 基类(LLM调用/JSON解析)
│   │   ├── ceo.py               # 总裁
│   │   ├── purchasing.py        # 采购(找货源)
│   │   ├── pricing.py           # 比价(算利润)
│   │   ├── review.py            # 审核(一审+复核)
│   │   ├── packaging.py         # 包装上架
│   │   ├── operations.py        # 运营(客服议价)
│   │   ├── accounting.py        # 会计
│   │   └── risk_control.py      # 风控
│   ├── models/                  # 数据模型
│   │   ├── database.py          # SQLAlchemy模型
│   │   └── repo.py              # 数据仓库(CRUD+统计)
│   └── tools/                   # 工具层
│       ├── xianyu_web.py        # Playwright网页客户端
│       ├── xianyu_messaging.py  # WebSocket消息监听
│       ├── source_platforms.py  # 拼多多/1688货源搜索
│       ├── risk_control.py      # 风控工具
│       └── llm_client.py        # LLM调用封装(演示模式)
├── tools/
│   └── export_cookie.py         # Cookie导出工具(3种方法)
└── data/                        # 数据库/日志/Cookie
```

## 核心设计

### 无货源倒卖模式

从拼多多/1688 低价采购，在闲鱼加价出售，赚差价：
- 货源搜索：并发检索拼多多+1688，按价格升序去重
- 利润计算：利润 = 闲鱼售价 - 货源价 - 运费(3元) - 平台费(0.6%)
- 利润红线：单笔毛利 >= 10 元，毛利率 >= 15%
- 心理定价：9.9 / 19.9 / 29.9 等价位

### 担保交易追踪

闲鱼采用支付宝担保交易，资金流为：买家付款 -> 担保账户冻结 -> 卖家发货 -> 买家确认(或10天超时) -> 放款到账。
系统自动追踪每笔回款状态，超期未放款的订单会触发风控预警。

### 风控机制

- 频率控制：所有操作经过 RateLimiter，模拟人类节奏
- 发布限制：新号72h内 <= 5条，每日发布 <= 5条
- 擦亮限制：每日 <= 20次，分散在流量高峰
- 自动暂停：检测到异常自动暂停，等待人工确认

### 议价策略

- 第一次砍价：不降价，用包邮引导
- 第二次砍价：降5%或送配件
- 第三次砍价：降10%（仍需保毛利 >= 5元）
- 低于成本：坚决拒绝

### 演示模式

无 API Key 时自动降级为演示模式：
- LLM 返回预设的合理响应（非空字符串）
- 货源搜索降级为内置模拟数据（10条/平台）
- 消息监听使用 MockMessageSource 生成模拟买家消息
- 商品发布不实际调用闲鱼，记录为模拟发布

## 大模型配置

| 模型 | 用途 | 配置名 | 价格(输出) |
|------|------|--------|-----------|
| DeepSeek V3.1 | 总裁/比价/运营/会计/风控 | deepseek_v3 | 8元/百万token |
| DeepSeek R1 | 审核(一审)推理 | deepseek_r1 | 16元/百万token |
| Qwen-Turbo | 采购(高频低复杂度) | qwen_turbo | 6元/百万token |
| Qwen-Plus | 包装上架(创意生成) | qwen_plus | 2元/百万token |

## 定时任务

| 任务 | 频率 | 说明 |
|------|------|------|
| 担保交易回款检查 | 每5分钟 | 追踪超期未放款订单 |
| 风控健康检查 | 每小时 | 账号安全状态扫描 |
| 选品上架流水线 | 每4小时 | 找货 -> 比价 -> 审核 -> 发布 |
| 总裁制定策略 | 每天09:00 | 更新选品方向 |
| 生成日报 | 每天22:00 | 运营数据汇总 + 总裁批示 |

## 后续阶段

- **阶段三**：多账号矩阵管理、数据分析优化、本地模型降本

## 合规提醒

- 闲鱼用户协议禁止使用脚本/机器人操作账户，自用仍有封号风险
- 本系统仅供学习研究，请用次要账号测试
- 绝不用于商业化出售自动化工具
