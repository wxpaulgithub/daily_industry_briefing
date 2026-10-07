# AI 商机日报 V2 技术实施规划

> 项目：`wxpaulgithub/daily_industry_briefing`  
> 目标：在现有“智能仓储每日简讯”基础上，升级出一个由 AI 驱动的智能仓储商机研究与企业微信自动推送系统。  
> 规划日期：2026-10-07  
> 建议开发基线：`codex/opportunity-tab-v1`，不要直接从 `master` 重新起炉灶。

---

## 1. 项目目标

当前系统已经具备较成熟的资讯采集、定时运行、网页展示、历史归档、健康检查和部署能力，但“商机质量”仍主要依赖固定关键词、规则筛选和简单评分。

本次 V2 的目标不是推翻现有项目，而是完成一次职责重构：

- **现有项目继续负责：**
  - 定时任务
  - 候选信息采集
  - HTTP 请求、超时、缓存、冷却
  - JSON 存储
  - 页面展示
  - 历史归档
  - 企业微信推送
  - Docker 部署
  - GitHub Actions 自动部署
  - 健康检查

- **AI 新增负责：**
  - 主动搜索市场商机
  - 扩展搜索角度
  - 阅读和理解项目内容
  - 识别项目阶段
  - 判断是否仍可介入
  - 查找更可靠的官方来源
  - 判断项目是否真正属于智能仓储
  - 评估与公司业务的匹配度
  - 识别重复项目和阶段变化
  - 生成 Top 5 商机日报
  - 生成“可能切入点”

最终系统要实现：

```text
07:00  原资讯系统正常运行

07:20  AI 商机任务开始
        ↓
候选池 + OpenAI Web Search
        ↓
规则粗筛
        ↓
AI 深度研究
        ↓
官方来源验证
        ↓
历史项目比对
        ↓
公司适配度评分
        ↓
Top 5
        ↓
保存 JSON
        ↓
商机 Tab 更新
        ↓
企业微信自动推送
```

---

# 2. 当前项目现状梳理

## 2.1 `master` 当前能力

当前 `master` 已经拥有：

```text
FastAPI
APScheduler
Skill 插件式数据源架构
RSS / HTML / 搜索采集
今日头条
微信公众号 RSS
政府采购 / 公共资源
知乎 / B站
本地项目
图片本地化
Jinja2 页面
JSON 日归档
企业微信 RSS 告警
Docker Compose
GitHub Actions 自动部署
```

当前主流程：

```text
Skill 并发抓取
    ↓
filter_relevant()
    ↓
deduplicate()
    ↓
score_article()
    ↓
select_articles()
    ↓
output/YYYY-MM-DD.json
    ↓
HTML
```

这个体系适合：

> “今天行业里发生了什么？”

但不完全适合：

> “今天有哪些项目值得销售跟进？”

---

## 2.2 当前规则筛选的主要问题

### 问题 1：关键词既承担“发现”，又承担“判断”

例如当前存在非常宽泛的准入词：

```text
制造
工厂
自动化
智能
仓储
物流
工业
设备
项目
招标
```

这些词适合做“高召回候选发现”，但不适合做最终商机判断。

因此容易出现：

```text
物流运输服务
普通仓库租赁
通用工业设备
泛自动化新闻
机器人行业资讯
已结束项目
```

进入高排名。

---

### 问题 2：固定 Query 无法覆盖真实项目的多样命名

真实商机可能不会写：

```text
智能仓储
自动化立体库
WMS
```

而可能写：

```text
包装线智能一体化
成品物流系统
器材库升级
生产物流系统
仓库标准化改造
自动装车系统
物料配送系统
智能化设备采购
物流系统设备
```

纯规则 Query 很容易漏掉。

---

### 问题 3：规则不能真正理解上下文

例如：

```text
“物流中心建设”
```

可能是：

- 纯土建
- 普通仓库
- 自动化仓库
- 冷库
- 分拨中心
- 有 WMS 的智能物流中心

规则无法稳定区分。

---

### 问题 4：当前评分缺少 Company Fit

项目可能非常“智能仓储”，但并不适合公司实际能力。

例如：

```text
机场行李系统 2亿元
```

仓储相关度可能很高，但公司适配度低。

而：

```text
制造企业
1200货位
11m
1.5吨
堆垛机 + 输送 + WMS
预算 300~500 万
```

则应明显优先。

V2 必须加入：

```text
company_fit_score
```

---

# 3. `codex/opportunity-tab-v1` 的处理原则

当前已有分支：

```text
codex/opportunity-tab-v1
```

该分支已经建立：

```text
services/opportunity/
├── models.py
├── fetcher.py
├── rules.py
├── scoring.py
├── enrichment.py
├── storage.py
└── sources/
```

并增加：

```text
国内 | 商机 | 本地 | 公众号 | 发现
```

同时已有：

```text
output/opportunities/YYYY-MM-DD.json
output/opportunities/index.json

GET /api/opportunities/today
GET /api/opportunities/{date}
POST /api/refresh?scope=opportunity
```

以及独立 07:20 商机定时任务。

### 结论

**保留这个分支作为 V2 开发基线。**

不要：

```text
废掉现有商机模块
重新从 master 开发
```

而应该：

```text
codex/opportunity-tab-v1
        ↓
替换“规则发动机”
        ↓
AI Opportunity V2
```

---

# 4. V2 总体架构

```text
                         ┌──────────────┐
                         │ 原招投标 Skill │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │ 今日头条搜索   │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │ 政府采购平台   │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │ 本地项目等     │
                         └──────┬───────┘
                                │
                        Candidate Pool
                                │
                                ├──────────────────────┐
                                │                      │
                                ▼                      ▼
                       规则粗筛                 OpenAI Web Search
                                │                      │
                                └──────────┬───────────┘
                                           ▼
                                  AI Research Layer
                                           │
                                  ┌────────┴────────┐
                                  ▼                 ▼
                           官方来源验证         项目语义理解
                                  │                 │
                                  └────────┬────────┘
                                           ▼
                                    Project Memory
                                           │
                                           ▼
                                      Company Fit
                                           │
                                           ▼
                                        Ranking
                                           │
                                           ▼
                                         Top 5
                                           │
                       ┌───────────────────┼───────────────────┐
                       ▼                   ▼                   ▼
                    JSON存档             商机Tab           企业微信
```

---

# 5. 目录结构调整

建议目标结构：

```text
services/
├── fetcher.py
├── generator.py
├── notifier.py
├── opportunity/
│   ├── __init__.py
│   ├── models.py
│   ├── fetcher.py
│   ├── rules.py
│   ├── scoring.py
│   ├── enrichment.py
│   ├── storage.py
│   │
│   ├── ai_client.py
│   ├── ai_prompt.py
│   ├── ai_researcher.py
│   ├── providers/
│   │   ├── base.py
│   │   ├── openai_provider.py
│   │   ├── glm_provider.py
│   │   └── __init__.py
│   ├── search/
│   │   ├── base.py
│   │   ├── openai_web_search.py
│   │   └── existing_search.py
│   ├── verifier.py
│   ├── company_fit.py
│   ├── project_memory.py
│   ├── publisher.py
│   ├── schemas.py
│   └── sources/
│       ├── __init__.py
│       ├── bidding.py
│       ├── search.py
│       └── existing_skills.py
```

配置：

```text
config_data/
├── opportunity_queries.json
├── company_profile.json
└── opportunity_feedback.json
```

输出：

```text
output/
└── opportunities/
    ├── 2026-10-08.json
    ├── 2026-10-09.json
    └── index.json
```

运行数据：

```text
runtime/
├── opportunity_ai_status.json
├── opportunity_publish_status.json
└── opportunity_usage.json
```

---

# 6. 各文件职责

## `models.py`

保留现有 `ProjectOpportunity`，但扩充字段。

建议：

```python
@dataclass
class ProjectOpportunity:
    title: str
    source_url: str

    source_name: str = ""
    discovery_url: str = ""
    official_source_url: str = ""

    owner: str = ""
    province: str = ""
    city: str = ""

    published: str = ""
    published_ts: float = 0.0
    deadline: str = ""

    budget: str = ""
    budget_amount: float | None = None

    summary: str = ""

    project_type: str = "UNKNOWN"
    stage: str = "UNKNOWN"

    technical_scope: list[str] = field(default_factory=list)

    relevance_score: float = 0
    freshness_score: float = 0
    source_score: float = 0
    company_fit_score: float = 0
    final_score: float = 0

    priority: str = "watch"

    opportunity_reason: str = ""
    entry_point: str = ""

    evidence: list[dict] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)

    first_seen_at: float = 0
    last_seen_at: float = 0
    last_reported_at: float = 0

    project_key: str = ""

    is_new: bool = False
    is_updated: bool = False
```

---

# 7. LLM 调用方式：必须做成多 Provider 架构

V2 **不得把商机系统绑定死在 OpenAI**。

OpenAI 可以作为首个实现和质量基准，但系统架构必须允许切换到：

```text
OpenAI
智谱 GLM
通义千问 / 阿里云百炼
DeepSeek
其他兼容 API 的 LLM
```

原因：

- 不同模型价格差异明显；
- 中文项目理解能力可能各有优势；
- 不同服务商的联网搜索、工具调用能力不同；
- 单一供应商不可用时应能降级；
- 后续可以做同一批商机的 A/B 质量测试。

推荐抽象：

```text
Opportunity Pipeline
        ↓
LLMProvider
        ├── OpenAIProvider
        ├── GLMProvider
        └── FutureProvider
```

业务层只能调用统一接口，例如：

```python
result = await llm.research_opportunity(candidate)
```

业务代码中不得出现：

```python
if openai:
    ...
```

到处散落的 Provider 专属逻辑。

---

## 7.1 OpenAI Provider

OpenAI 实现优先采用：

```text
Responses API
+
web_search
+
Structured Outputs
```

它适合作为第一版基准实现，因为能够将模型推理、联网搜索和结构化结果放在一条研究链路内。

但这些能力必须封装在：

```text
services/opportunity/providers/openai_provider.py
```

而不是直接写进 `fetcher.py` 或业务流程。

---

## 7.2 GLM Provider

智谱 GLM API 可以作为可选 LLM Provider。

实现目标不是要求 GLM 与 OpenAI 的每一个工具接口完全一致，而是保证它最终满足统一的业务能力：

```text
discover()
research()
structure_output()
```

如果所选 GLM API / 智能体接口支持联网搜索，则由 `GLMProvider` 使用其原生搜索工具；如果不支持或稳定性不足，则采用“外部 SearchProvider + GLM 分析”的组合。

推荐结构：

```text
GLMProvider
    ↓
候选内容 / 搜索结果
    ↓
GLM 负责语义分析、项目理解、Company Fit、结构化输出
```

因此不能把：

```text
LLM能力
=
Web Search能力
```

写死为同一个供应商。

---

## 7.3 LLM 与 Search 必须分层

这是多模型架构最重要的改动。

推荐：

```text
LLMProvider
负责：
- 项目理解
- 阶段判断
- Company Fit
- 摘要
- 切入点
- 最终排序解释

SearchProvider
负责：
- 网络检索
- 找官方来源
- 扩展搜索
- 返回URL与网页摘要
```

实现为：

```text
services/opportunity/providers/
├── base.py
├── openai_provider.py
├── glm_provider.py
└── __init__.py

services/opportunity/search/
├── base.py
├── openai_web_search.py
├── provider_native_search.py
└── existing_search.py
```

在 OpenAI 模式下可以是：

```text
OpenAI LLM
+
OpenAI Web Search
```

在 GLM 模式下可以是：

```text
GLM
+
GLM 原生联网搜索
```

或者：

```text
GLM
+
现有搜索源 / 独立搜索Provider
```

---

## 7.4 Provider 通过环境变量切换

`.env`：

```env
LLM_PROVIDER=openai

# OpenAI
OPENAI_API_KEY=
OPENAI_OPPORTUNITY_MODEL=

# GLM
GLM_API_KEY=
GLM_OPPORTUNITY_MODEL=

# 通用
OPPORTUNITY_AI_REASONING=medium
OPPORTUNITY_AI_RESEARCH_LIMIT=12
OPPORTUNITY_DIGEST_LIMIT=5
```

切换时只需要：

```env
LLM_PROVIDER=glm
```

不要修改业务代码。

---

## 7.5 Provider 能力声明

每个 Provider 应声明能力：

```python
ProviderCapabilities(
    native_web_search=True,
    structured_output=True,
    tool_calling=True,
    reasoning_control=True,
)
```

商机流水线根据能力决定：

```text
如果 native_web_search=True
→ 可使用模型原生搜索

如果 native_web_search=False
→ 使用独立 SearchProvider

如果 structured_output=False
→ 使用 JSON + Pydantic 校验 + 重试
```

---

## 7.6 模型必须可配置

任何模型名都不得写死在业务代码。

例如：

```env
LLM_PROVIDER=openai
OPENAI_OPPORTUNITY_MODEL=<current-model>
```

或：

```env
LLM_PROVIDER=glm
GLM_OPPORTUNITY_MODEL=<current-glm-model>
```

具体模型选择应通过真实商机数据 A/B 测试决定，而不是在架构文档中永久指定某个型号。

---

# 8. AI 任务拆成两阶段

不要使用“一次 Prompt 搜索完全部并直接写日报”。

---

## 阶段 A：Discovery

目标：

> 尽可能找到真实候选项目。

AI 每天主动搜索：

```text
新建立库
自动化仓库
仓储改造
堆垛机
堆垛机大修
WMS
WCS
生产物流
包装后端物流
自动装车
AGV仓储
AMR仓储
输送系统
新工厂
扩产
生产基地
物流中心
备件仓
器材库
成品仓
原料仓
维保
智能化设备
```

搜索不限于固定 Query。

要求 AI：

```text
如果某一种搜索方式发现新的高价值行业表述，
主动扩展下一轮搜索词。
```

### 输出

最多：

```text
20~30 个候选
```

而不是直接 Top 5。

---

# 9. Candidate Pool 的来源

每日候选池由两部分组成：

## A. 现有代码候选

复用：

```text
BiddingSkill
LocalProjectSkill
ToutiaoSkill
官方公告列表
```

只取候选，不让其直接决定最终排名。

---

## B. AI Web Search 候选

AI 主动发现现有系统没抓到的项目。

最终：

```text
existing_candidates
+
ai_discovery_candidates
        ↓
merged_candidate_pool
```

---

# 10. 规则层重新定位

`rules.py` 不删除。

但角色从：

> 最终裁判

改成：

> 便宜的第一层过滤器

例如：

```text
原始候选 300
↓
硬排除
↓
100
↓
AI判断
```

---

## 规则应该继续负责

适合规则的：

```text
明显运输服务
仓库租赁
物业
食材配送
办公用品
医疗耗材
已超过极长时间
无URL
重复URL
```

---

## 规则不再负责

不要再用规则最终判断：

```text
是不是值得跟
是不是生产物流
项目到底属于哪个技术路线
公司是否适合
项目当前还有没有销售价值
```

这些交给 AI。

---

# 11. AI Research 阶段

对粗筛后的 Top 10~15 个候选进行深度研究。

每个候选研究：

```text
1. 项目是否真实存在？
2. 最可靠来源是什么？
3. 谁是业主？
4. 地点？
5. 项目阶段？
6. 发布时间？
7. 投标/报名截止？
8. 预算？
9. 技术范围？
10. 是否包含仓储自动化？
11. 是否包含：
    - 堆垛机
    - 输送
    - WMS
    - WCS
    - AGV
    - AMR
    - 四向车
    - 穿梭车
    - 提升机
    - 分拣
    - 机器人
12. 是否已中标？
13. 是否已关闭？
14. 当前还有没有介入空间？
15. 对公司可能的切入点是什么？
```

---

# 12. 官方来源验证

新增：

```text
verifier.py
```

来源优先级：

```text
Tier 1
政府采购网
公共资源交易平台
企业官网
央国企采购平台
官方招投标平台

Tier 2
行业协会
权威媒体
正式采购代理机构页面

Tier 3
聚合网站
搜索结果
转载
今日头条
```

---

## Source URL 策略

例如首先在聚合站发现：

```text
某公司自动化立库采购
```

则：

```text
discovery_url = 聚合站
```

继续查官方：

```text
official_source_url = 企业官网 / 招标平台
source_url = official_source_url
```

如果官方来源找不到：

```text
source_url = discovery_url
source_tier = 3
risk_flags += "official_source_not_found"
```

---

# 13. Evidence 机制

这是 V2 非常重要的新能力。

AI 输出的关键事实必须有 evidence。

例如：

```json
{
  "field": "budget",
  "value": "833.12万元",
  "source_title": "智能化仓储管理设备采购项目",
  "source_url": "https://...",
  "quote": "预算金额：833.12万元"
}
```

注意：

- quote 只保留短证据
- 不保存大段版权文本
- 一个字段可有多个证据
- 没证据则字段为空，不猜

---

# 14. Structured Outputs

AI 结果必须使用严格 Schema。

不要：

```text
请返回 JSON
```

而应该使用：

```text
Structured Outputs
JSON Schema
strict=true
```

例如：

```python
class OpportunityResearchResult(BaseModel):
    title: str
    owner: str
    province: str
    city: str
    budget_text: str
    deadline: str
    stage: Literal[
        "EARLY_SIGNAL",
        "PROCUREMENT",
        "AWARD",
        "CLOSED",
        "UNKNOWN"
    ]
    project_type: Literal[
        "NEW_BUILD",
        "RETROFIT",
        "MAINTENANCE",
        "SOFTWARE",
        "EQUIPMENT",
        "MIXED",
        "UNKNOWN"
    ]
    technical_scope: list[str]
    summary: str
    opportunity_reason: str
    entry_point: str
    source_urls: list[str]
    evidence: list[EvidenceItem]
```

---

# 15. Prompt 设计

新增：

```text
services/opportunity/ai_prompt.py
```

Prompt 不写在业务代码中。

建议拆成：

```python
DISCOVERY_INSTRUCTIONS
RESEARCH_INSTRUCTIONS
FINAL_RANKING_INSTRUCTIONS
```

---

# 16. Discovery Prompt 核心要求

示例逻辑：

```text
你是中国大陆智能仓储项目研究员。

目标不是收集行业新闻，而是寻找真实潜在采购机会。

重点领域：
- 自动化立体库
- 堆垛机
- 输送系统
- WMS
- WCS
- AGV/AMR
- 四向车/穿梭车
- 自动分拣
- 生产物流
- 包装后端物流
- 旧立库升级
- 维保大修

优先：
- 制造企业
- 新工厂
- 扩产
- 技改
- 仓库升级
- 招标
- 采购意向
- 询价
- 维保

不要：
- 泛行业新闻
- 纯物流运输服务
- 仓库出租
- 已经结束很久的项目
- 单纯概念宣传
```

---

# 17. Company Profile

新增：

```text
config_data/company_profile.json
```

建议初始内容：

```json
{
  "company_positioning": "面向中国中小制造企业的智能仓储集成商",

  "ideal_project_budget_min": 500000,
  "ideal_project_budget_max": 5000000,

  "acceptable_project_budget_max": 10000000,

  "strengths": [
    "堆垛机制造",
    "非标机械",
    "输送系统",
    "WMS",
    "WCS",
    "中小型定制集成"
  ],

  "preferred_project_types": [
    "新建立库",
    "旧立库改造",
    "堆垛机升级",
    "WMS/WCS",
    "维保"
  ],

  "preferred_customer_types": [
    "机械制造",
    "汽车零部件",
    "化工新材料",
    "电子制造",
    "家电制造",
    "中小制造企业"
  ],

  "weaknesses": [
    "超大型总包经验有限",
    "电气现场能力仍在建设",
    "AGV本体非自研"
  ]
}
```

以后 AI 每次 Research / Ranking 都读取。

---

# 18. Company Fit 评分

建议：

```text
company_fit_score = 0~100
```

考虑：

### +30

项目内容与核心能力直接匹配：

```text
堆垛机
输送
WMS
WCS
```

### +20

制造企业终端用户

### +15

预算在 50~500 万

### +10

500~1000 万

### +15

改造 / 维保 / 中小型非标

### +10

江苏 / 上海 / 浙江 / 安徽等较便利区域

---

## 降权

例如：

```text
纯软件 SaaS
大型机场系统
港口超大型系统
几亿元 EPC
教学实训
纯科研
已中标
已正式投产
```

不是全部剔除，而是降低：

```text
company_fit_score
```

---

# 19. 最终评分

建议：

```text
final_score =
warehouse_relevance × 0.30
+ stage_value        × 0.20
+ freshness          × 0.15
+ source_quality     × 0.10
+ company_fit        × 0.25
```

相比 V1，提高 Company Fit 权重。

---

## Stage 分

```text
PROCUREMENT      100
EARLY_SIGNAL      85
AWARD             25
CLOSED             0
UNKNOWN           30
```

---

## Freshness

```text
24小时以内       100
1~3天             90
4~7天             75
8~15天            50
15天以上           0~20
```

早期信号可适度放宽。

---

# 20. Project Memory

当前 `output/opportunities/index.json` 可以继续使用。

但增加：

```text
first_seen_at
last_seen_at

last_reported_at
last_reported_stage
last_digest_hash

previous_stage
previous_budget
previous_deadline
```

---

## 推送去重规则

### 情况 A

昨天：

```text
PROCUREMENT
```

今天：

```text
PROCUREMENT
```

没有明显变化：

```text
不再推
```

---

### 情况 B

昨天：

```text
EARLY_SIGNAL
```

今天：

```text
PROCUREMENT
```

则：

```text
重新进入 Top 5
标记“项目更新”
```

---

### 情况 C

昨天：

```text
PROCUREMENT
```

今天：

```text
AWARD
```

一般不进入销售 Top 5。

但可以作为：

```text
市场情报更新
```

---

# 21. Project Key

当前简单 title+owner+city hash 继续保留，但要增强规范化。

去掉：

```text
招标公告
采购公告
中标公告
中标候选人
二次
重新招标
变更公告
澄清
```

例如：

```text
XX公司自动化立库采购公告
XX公司自动化立库二次招标
XX公司自动化立库中标候选人
```

应识别成同一个项目。

---

# 22. AI 不直接覆盖确定性规则

必须坚持：

> 规则负责确定性，AI负责语义判断。

例如：

规则负责：

```text
日期计算
预算数字转换
URL标准化
project_key
重复Hash
截止日期比较
Webhook状态
文件存储
```

AI负责：

```text
项目含义
技术范围
当前阶段
业务价值
公司适配度
切入方式
```

---

# 23. 企微推送模块

新增：

```text
services/opportunity/publisher.py
```

环境变量：

```env
WECOM_OPPORTUNITY_WEBHOOK_URL=https://...
```

---

## 不复用 RSS 告警 Webhook

现有：

```env
WECOM_WEBHOOK_URL
```

继续只负责：

```text
RSS失效
Cookie失效
系统告警
```

新的：

```env
WECOM_OPPORTUNITY_WEBHOOK_URL
```

只负责：

```text
商机日报
重要项目更新
```

---

# 24. 企业微信推送格式

不要把完整日报全文直接塞进群。

推荐：

```text
📌 智能仓储商机日报｜2026-10-08

今日筛选 5 条
A级 2 条 · B级 2 条 · 观察 1 条

🔴 1. XX公司自动化立体库
江苏 · 约380万元 · 公开招标
涉及：堆垛机 / 输送 / WMS / WCS
切入：建议立即获取技术文件

🔴 2. XX公司老立库升级
浙江 · 预算待核实 · 采购前期
涉及：堆垛机 / PLC / WCS
切入：适合改造类项目跟踪

🟠 3. ...

👉 查看完整日报
https://mag.mhstar.tech/?scope=opportunity
```

---


# 24A. AI 商机结果的双出口要求（必须实现）

OpenAI API 研究生成的商机结果不是只用于“企业微信推送”，也不是只用于“商机 Tab”。

**同一份最终商机数据必须同时服务两个出口：**

```text
OpenAI API + Web Search
        ↓
AI 商机研究结果
        ↓
统一结构化 Opportunity JSON
        ↓
┌────────────────────┬────────────────────┐
↓                    ↓
资讯杂志“商机”Tab      企业微信日报推送
```

也就是说：

- AI 只生成一次最终结果；
- 页面和企业微信都从同一个 `ProjectOpportunity[]` / 同一个每日 JSON 读取；
- 不允许网页使用一套商机、企微再重新生成另一套商机；
- 不允许为了企微推送再次调用 OpenAI 重新总结，避免内容不一致；
- 企业微信只是同一份商机数据的“摘要分发出口”。

推荐唯一数据源：

```text
output/opportunities/YYYY-MM-DD.json
```

页面：

```text
/?scope=opportunity
```

企业微信：

```text
publisher.py
→ 读取当天最终 Top 5
→ 生成群消息
→ WECOM_OPPORTUNITY_WEBHOOK_URL
```

这样才能保证：

```text
同事在企业微信群看到的项目
=
点击进入“商机”Tab 后看到的项目
```

---

# 24B. “商机”Tab 前端风格要求：必须与现有其他 Tab 完全一致

这是 V2 的强制验收条件，不是可选优化。

当前资讯杂志已经形成统一页面体系：

```text
国内 | 商机 | 本地 | 公众号 | 发现
```

其中“商机”Tab 必须继续复用现有资讯杂志的整体视觉体系，不能开发成另一个独立后台页面或另一套 UI。

## 必须保持一致的部分

与“国内 / 本地 / 公众号 / 发现”完全一致：

```text
页面最大宽度
整体背景
页头
Logo
主标题
日期
字体体系
三套主题（纸 / 简 / 深）
Tab 导航
页脚
刷新按钮
移动端响应式
留白比例
卡片边距
标题字号体系
图片尺寸与圆角规则
hover / transition
```

实现原则：

> **复用现有 `templates/magazine.html` 的页面壳层和 CSS，只针对商机卡片中间的数据字段做差异化展示。**

不要新建一套：

```text
opportunity_dashboard.html
```

除非只是内部局部模板并最终嵌入原有壳层。

推荐：

```text
magazine.html
    ↓
根据 scope 判断
    ↓
national / local / wechat / discover
→ Article 卡片

opportunity
→ Opportunity 卡片
```

## 可以不同的只有“内容字段”

商机卡片可以比普通资讯多展示：

```text
优先级
项目阶段
地区
预算
截止时间
技术范围
为什么值得关注
可能切入点
```

但视觉语言仍必须属于同一个杂志系统。

例如：

```text
普通资讯卡片
来源 · 日期
标题
摘要

商机卡片
A级 · 公开招标 · 江苏 · 380万元
标题
摘要
涉及：堆垛机 / 输送 / WMS
切入：建议立即获取技术文件
```

不是：

```text
突然变成表格后台
复杂仪表盘
ERP风格卡片
Bootstrap管理界面
```

## 商机首条 Featured Card

继续复用现有头条 Featured Card：

```text
大图
来源/阶段/优先级
大标题
摘要
```

不要因为是商机就改变整个首页构图。

## 图片策略也保持一致

商机有原图：

```text
使用项目原图
```

没有原图：

```text
继续使用资讯杂志既有 fallback 图片机制
或按商机类型选择统一 fallback
```

但尺寸、比例、卡片结构必须与现有 Tab 一致。

---

# 24C. 企业微信推送的视觉与内容要求

企业微信不是网页，因此**不要求与资讯杂志逐像素一致**。

它的目标不是“复制网页”，而是：

> 快速提醒 + 快速判断 + 引导打开完整商机页面。

因此可以采用更适合聊天场景的紧凑格式。

推荐结构：

```text
📌 智能仓储商机日报｜2026-10-08

今日筛选 5 条
A级 2 条 · B级 2 条 · 观察 1 条

🔴 1. XX公司自动化立体库
江苏 · 380万元 · 公开招标
堆垛机 / 输送 / WMS / WCS
切入：建议立即获取技术文件

🟠 2. XX公司老立库升级
浙江 · 预算待核实 · 采购前期
堆垛机 / PLC / WCS
切入：适合改造类项目跟踪

👉 查看完整日报
https://mag.mhstar.tech/?scope=opportunity
```

## 企业微信可以参考资讯杂志的品牌风格

例如保持：

```text
“智能仓储商机日报”命名
简洁
克制
少装饰
强调层级
突出项目名称
突出优先级
```

但不必强行模仿网页：

```text
字体
卡片
背景
图片布局
```

因为企微机器人消息能力有限，移动端阅读效率优先。

## 企业微信推送必须包含完整页面入口

必须始终附带：

```text
https://mag.mhstar.tech/?scope=opportunity
```

或通过 `SITE_URL` 动态生成：

```python
f"{SITE_URL}/?scope=opportunity"
```

不要写死域名。

## 推荐职责分工

```text
企业微信
= 今日有哪些项目值得立即看

商机 Tab
= 为什么值得看、证据是什么、完整来源是什么
```

---

# 24D. 页面与企微内容一致性要求

企业微信不是独立内容生产链。

正确：

```text
AI Research
↓
最终 Top 5
↓
保存 JSON
↓
├─ 商机 Tab
└─ 企业微信摘要
```

错误：

```text
AI生成网页Top5
+
再调用一次AI生成企微Top5
```

必须避免：

```text
网页第一名 = A项目
企微第一名 = B项目

网页预算 = 380万元
企微预算 = 350万元
```

因此 `publisher.py` 只能消费已经保存成功的最终数据。

建议：

```python
items = load_snapshot(today)
message = build_wecom_digest(items[:5])
await send_wecom(message)
```

而不是：

```python
await openai_generate_wecom_digest(...)
```

---


# 25. 推送时机

严格顺序：

```text
AI Research完成
↓
JSON保存成功
↓
Project Memory更新成功
↓
页面可读取
↓
企业微信推送
```

绝不能：

```text
先推送
↓
再写JSON
```

否则可能出现：

> 群里收到，但网页没有。

---

# 26. 推送失败策略

企业微信失败：

```text
不能导致商机任务失败
```

记录：

```text
runtime/opportunity_publish_status.json
```

例如：

```json
{
  "date": "2026-10-08",
  "status": "failed",
  "retry_count": 1
}
```

可以：

```text
10分钟后重试一次
```

最多：

```text
2~3次
```

避免无限发送。

---

# 27. 图片策略

企微摘要 V1 不要求逐条图片。

网页商机页：

优先：

```text
官方项目页面图片
```

没有：

```text
根据项目类型选 fallback
```

例如：

```text
NEW_BUILD       → 立库
RETROFIT        → 老库改造
MAINTENANCE     → 堆垛机检修
SOFTWARE        → WMS
AGV             → 移动机器人
```

避免为了图片再增加大量 AI / 图片搜索成本。

---

# 28. 商机 Tab 页面升级

当前 V1 已经复用 `magazine.html`。

继续沿用，但建议显示：

```text
项目名称

A级 / B级 / 观察
地区
预算
阶段

项目摘要

技术范围：
堆垛机 / 输送 / WMS / AGV

为什么值得关注：
...

可能切入：
...

原始官方来源 →
```

不要突出：

```text
final_score = 83.7
```

内部保留精确分数，前端显示：

```text
重点关注
值得跟进
观察
```

---

# 29. API 设计

保留：

```text
GET /api/opportunities/today
GET /api/opportunities/{date}
POST /api/refresh?scope=opportunity
```

新增：

```text
POST /api/opportunities/research
POST /api/opportunities/publish-test
POST /api/opportunities/publish-today
GET  /api/opportunities/status
```

---

## `/api/opportunities/status`

返回：

```json
{
  "enabled": true,
  "ai_enabled": true,
  "last_run": "...",
  "last_success": "...",
  "last_count": 5,
  "last_ai_candidates": 18,
  "last_web_search_calls": 10,
  "last_publish_status": "success",
  "webhook_configured": true
}
```

绝不能返回：

```text
API Key
Webhook URL
```

---

# 30. Scheduler

现有：

```text
07:00 普通资讯
07:20 商机
```

保留。

商机任务：

```python
scheduler.add_job(
    _do_fetch_opportunities,
    CronTrigger(hour=7, minute=20),
    max_instances=1,
    coalesce=True,
)
```

---

## 超时

AI任务比规则采集慢。

建议允许：

```text
15~20分钟
```

不要使用普通 Skill 的 45 秒超时限制。

---

# 31. AI Research 状态机

建议：

```text
IDLE
DISCOVERING
PREFILTERING
RESEARCHING
VERIFYING
RANKING
SAVING
PUBLISHING
DONE
FAILED
```

写入：

```text
runtime/opportunity_ai_status.json
```

方便调试。

---

# 32. 失败降级

这是生产系统必须有的。

---

## 主 LLM Provider 完全失败

则：

```text
使用 V1 规则系统产生候选
↓
保存为 fallback 商机
↓
页面仍有内容
↓
企业微信标题注明：
“今日AI研究不可用，以下为规则筛选结果”
```

---

## Web Search失败

可以：

```text
使用现有候选池
↓
AI仅分析已有候选
```

---

## AI结构化输出失败

```text
重试一次
```

仍失败：

```text
fallback规则
```

---

# 33. LLM / Search API 成本控制

建议通过四个手段控制。

---

## 1. 不让 AI 研究所有候选

例如：

```text
原始 300
↓
规则粗筛 60
↓
快速语义筛选 20
↓
深度研究 10
↓
Top 5
```

---

## 2. 控制 Web Search 调用数

Responses API 支持：

```text
max_tool_calls
```

建议：

```env
OPENAI_OPPORTUNITY_MAX_TOOL_CALLS=12
```

---

## 3. 限制输出 Token

例如：

```env
OPENAI_OPPORTUNITY_MAX_OUTPUT_TOKENS=8000
```

---

## 4. 每日记录 Usage

```text
runtime/opportunity_usage.json
```

记录：

```text
date
responses
web_search_calls
input_tokens
output_tokens
estimated_cost
```

当前 OpenAI Web Search 官方定价为每 1000 次调用 10 美元，另加模型 token 费用；因此真正需要重点控制的通常是研究模型 Token 和搜索调用数量。

---

# 34. 不建议第一版使用 Deep Research

虽然 Deep Research 质量可能更高，但：

```text
慢
成本高
流程更复杂
```

V2 第一阶段优先：

```text
Responses API
+
gpt-5.5
+
web_search
+
medium reasoning
```

如果后期发现：

```text
Top 5仍经常漏掉复杂项目
```

再针对：

```text
A级候选
```

增加深度研究。

---

# 35. 安全配置

`.env`：

```env
LLM_PROVIDER=openai

# OpenAI（使用时填写）
OPENAI_API_KEY=
OPENAI_OPPORTUNITY_MODEL=

# GLM（使用时填写）
GLM_API_KEY=
GLM_OPPORTUNITY_MODEL=

# 商机任务通用配置
OPPORTUNITY_AI_REASONING=medium
OPPORTUNITY_AI_RESEARCH_LIMIT=12
OPPORTUNITY_DIGEST_LIMIT=5

WECOM_OPPORTUNITY_WEBHOOK_URL=...
```

Docker Compose：

```yaml
- OPENAI_API_KEY=${OPENAI_API_KEY:-}
- OPENAI_OPPORTUNITY_MODEL=${OPENAI_OPPORTUNITY_MODEL:-gpt-5.5}
- OPENAI_OPPORTUNITY_REASONING=${OPENAI_OPPORTUNITY_REASONING:-medium}
- OPENAI_OPPORTUNITY_MAX_TOOL_CALLS=${OPENAI_OPPORTUNITY_MAX_TOOL_CALLS:-12}

- WECOM_OPPORTUNITY_WEBHOOK_URL=${WECOM_OPPORTUNITY_WEBHOOK_URL:-}
```

---

## 禁止

不得：

```text
写进 config.py 明文
写进 README
写进 Git commit
写进日志
API返回
```

---

# 36. requirements.txt

建议：

```text
openai          # OpenAI Provider
zhipuai         # 如采用智谱官方 SDK，可选
```

如果 GLM 通过兼容 HTTP/OpenAI 风格接口接入，也可以不强制引入 `zhipuai`，由 Provider 内部决定。

同时确保：

```text
pydantic >= 2
```

用于 Structured Outputs schema。

---

# 37. LLM Client / Provider Factory

新增：

```text
services/opportunity/ai_client.py
```

统一通过 Provider Factory 创建：

```python
provider = create_llm_provider(
    name=os.getenv("LLM_PROVIDER", "openai")
)
```

示意：

```python
class LLMProvider(Protocol):
    async def discover(self, ...): ...
    async def research(self, ...): ...
    async def rank(self, ...): ...
```

`OpenAIProvider`、`GLMProvider` 分别实现该接口。

其他模块不得自行实例化某一家 SDK Client。

这样便于：

```text
统一超时
统一重试
统一日志
统一usage
统一切换Provider
统一fallback
```

---

# 38. ai_researcher.py

核心接口建议：

```python
async def discover_with_ai(...) -> list[AICandidate]:
    ...

async def research_candidate(candidate) -> OpportunityResearchResult:
    ...

async def rank_opportunities(items) -> list[ProjectOpportunity]:
    ...
```

---

# 39. 不建议一次研究 30 个项目

推荐：

```text
候选初筛上限 40
AI深度研究上限 12
最终展示 5~10
企微推送 5
```

环境变量：

```env
OPPORTUNITY_AI_RESEARCH_LIMIT=12
OPPORTUNITY_DIGEST_LIMIT=5
```

---

# 40. 日报数量原则

不要强制：

```text
每天一定 5 条
```

更合理：

```text
最多 5 条
```

如果当天只有：

```text
2 条真正值得跟
```

则推：

```text
今日仅发现2条高质量商机
```

不要用垃圾信息补齐。

---

# 41. Priority

建议：

```text
A  >= 80
B  65~79
WATCH 55~64
DROP < 55
```

但额外规则：

```text
CLOSED
```

默认：

```text
不进入日报
```

---

# 42. Feedback 机制

保留已有：

```text
config_data/opportunity_feedback.json
```

建议变成：

```json
[
  {
    "project_key": "...",
    "decision": "reject",
    "reason": "transport_service",
    "created_at": "..."
  }
]
```

reason：

```text
not_warehouse
too_large
already_closed
too_old
duplicate
transport_service
pure_news
low_fit
teaching_project
```

---

# 43. 后续 AI 学习 Feedback

V2 不做模型训练。

先在 Prompt 中带入最近：

```text
20~50 条人工反馈
```

例如：

```text
过去人工判定：
- 机场行李系统：低适配
- 制造企业老立库升级：高适配
```

即可明显改善排序。

---


# 43A. 多 Provider 质量与成本评测

V2 不预设“OpenAI 一定最好”或“GLM 一定更便宜就一定更适合”。

正式选型必须基于同一批真实商机做 A/B 测试。

建议至少比较：

```text
OpenAI Provider
vs
GLM Provider
```

同一组 30~50 条候选，比较：

```text
真实商机识别率
漏报率
误报率
项目阶段判断准确率
预算/业主/截止日期抽取准确率
官方来源找到率
Company Fit 判断质量
最终 Top 5 与人工判断一致度
平均耗时
单次成本
```

推荐决策方式：

```text
质量优先
↓
质量接近时比较成本
↓
再比较稳定性与速度
```

最终甚至可以采用：

```text
主 Provider = GLM
高价值/疑难候选 = OpenAI复核
```

或反过来：

```text
主 Provider = OpenAI
低成本批量初筛 = GLM
```

架构必须允许这种组合，而不是只能二选一。

---

# 44. 测试体系

当前商机分支已经有：

```text
test_opportunity_rules.py
test_opportunity_scoring.py
test_opportunity_dedup.py
test_opportunity_enrichment.py
test_opportunity_sources.py
test_opportunity_routes.py
```

全部保留。

新增：

```text
test_ai_schema.py
test_company_fit.py
test_project_memory.py
test_publisher.py
test_ai_fallback.py
test_digest_builder.py
```

---

# 45. AI 测试不能依赖真实 API

普通 pytest：

```text
全部 mock OpenAI
```

真实 API 测试：

```text
scripts/test_opportunity_ai_live.py
```

手工运行。

避免 CI 每次花 API 钱。

---

# 46. 黄金测试集

建议建立：

```text
tests/fixtures/opportunity_gold.json
```

不少于：

```text
80~100 条
```

分类：

```text
真实高质量商机
运输服务
租赁
教学项目
行业新闻
已结束
中标
维保
新建立库
WMS
AGV
早期新工厂信号
```

---

# 47. 核心验收指标

### 商机相关性

Top 20：

```text
真正智能仓储/物流自动化相关 >= 90%
```

### 高价值商机

Top 5：

```text
具有真实项目意义 >= 90%
```

### 重复率

```text
< 10%
```

### 过期信息

```text
Top 5 不得出现明显已失效旧项目
```

除非：

```text
有阶段更新
```

---

# 48. 与当前 ChatGPT 日报对照测试

上线后至少：

```text
连续 2~4 周
```

保留现在 ChatGPT 日报。

每天比较：

```text
ChatGPT Top 5
vs
AI系统 Top 5
```

记录：

```text
missed
false_positive
bad_ranking
wrong_stage
wrong_budget
duplicate
```

---

# 49. 页面与推送各自职责

## 企业微信

负责：

```text
发现提醒
快速判断
```

---

## 商机 Tab

负责：

```text
完整阅读
历史查看
详细来源
技术范围
项目阶段
```

---

# 50. 现有四个 Tab 不动

必须确保：

```text
国内
本地
公众号
发现
```

完全不受 AI 商机模块影响。

这是上线安全的重要原则。

---

# 51. 建议开发阶段

## Phase 0：整理基线

目标：

```text
确认 codex/opportunity-tab-v1 可以正常测试
```

动作：

- rebase/更新到最新 master
- 跑现有 pytest
- 确认商机 Tab
- 确认 API
- 确认 07:20 scheduler

---

## Phase 1：配置和模型升级

完成：

```text
models.py
schemas.py
company_profile.json
project_memory.py
```

暂时不接 OpenAI。

---

## Phase 2：OpenAI Discovery

新增：

```text
ai_client.py
ai_prompt.py
ai_researcher.py
```

实现：

```text
Web Search
→ 候选输出
```

---

## Phase 3：AI Research + Verification

实现：

```text
候选深度研究
官方来源验证
Evidence
Structured Outputs
```

---

## Phase 4：Ranking

实现：

```text
Company Fit
项目历史
去重
Top 5
```

---

## Phase 5：企业微信

实现：

```text
publisher.py
Webhook
digest
测试接口
```

---

## Phase 6：UI

升级商机 Tab。

---

## Phase 7：灰度

连续：

```text
2周
```

AI商机正常生成，但：

```text
先手工检查
```

再决定完全自动推送。

---

# 52. Commit 建议

不要一个大 commit。

建议：

```text
1. refactor: prepare opportunity v2 data model

2. feat: add company profile and project memory

3. feat: add OpenAI opportunity discovery

4. feat: add AI research and structured outputs

5. feat: add official source verification

6. feat: add company-fit ranking

7. feat: add WeCom opportunity publisher

8. feat: improve opportunity tab presentation

9. test: add AI opportunity eval fixtures

10. docs: add AI opportunity runbook
```

---

# 53. Branch 策略

当前：

```text
codex/opportunity-tab-v1
```

建议不要直接 merge。

两种方案均可：

### 方案 A

继续在该分支：

```text
codex/opportunity-tab-v1
```

完成 V2。

### 方案 B（更推荐）

从该分支创建：

```text
codex/opportunity-ai-v2
```

这样：

```text
V1规则版本
```

仍保留作为 fallback 和对照。

---

# 54. PR 策略

最终 PR：

```text
feat: add AI-powered smart warehouse opportunity research
```

PR 必须说明：

```text
旧资讯系统无行为变化
商机系统独立运行
OpenAI失败可降级
Webhook失败不影响商机生成
```

不要自动 merge。

---

# 55. 部署前检查

服务器 `.env`：

```text
OPENAI_API_KEY
OPENAI_OPPORTUNITY_MODEL
WECOM_OPPORTUNITY_WEBHOOK_URL
```

检查：

```bash
docker compose --env-file /opt/industry_briefing/.env config
```

确保变量进入容器。

---

# 56. 上线后 API 检查

```text
/api/status

/api/opportunities/status

/api/opportunities/today
```

然后手工：

```text
/api/refresh?scope=opportunity
```

---

# 57. 推送测试

新增：

```text
/api/opportunities/publish-test
```

只发：

```text
智能仓储商机日报

推送通道测试成功。
```

禁止包含真实商机。

确认后再：

```text
/api/opportunities/publish-today
```

---

# 58. 日志

每天至少记录：

```text
候选总数
规则粗筛数
AI发现数
AI研究数
官方源确认数
最终Top5
重复过滤数
过期过滤数
LLM调用数
Web Search调用数
AI耗时
总耗时
企业微信发送状态
```

例如：

```text
[OpportunityAI]
raw=263
prefilter=68
ai_discovered=24
research=12
verified=9
active=7
final=5
duration=214s
```

---

# 59. 不在 V2 做的事情

明确排除：

```text
数据库
Redis
Celery
Vector DB
CRM
自动发邮件
自动创建销售任务
LLM fine-tuning
多Agent复杂编排
完整客户数据库
微信小程序
```

先把：

> “每天稳定找到真正值得跟的5条商机”

做好。

---

# 60. 最终验收场景

每天早上：

```text
07:20
服务器自动开始商机研究

07:25~07:35
AI完成搜索和验证

07:35
output/opportunities/YYYY-MM-DD.json 更新

07:35
/?scope=opportunity 可查看

07:36
企业微信群收到：

智能仓储商机日报｜10月8日
今日5条，其中A级2条
...
```

---

# 61. 最重要的架构原则

整个 V2 最重要的不是：

```text
“给现有爬虫加一个AI总结”
```

而是：

```text
现有程序
负责高召回和基础设施

AI
负责搜索策略、理解、验证和商业判断
```

最终形成：

```text
程序负责“多找”
AI负责“找对”
规则负责“稳定”
历史库负责“不重复”
企业微信负责“送到人”
```

这就是 V2 与当前资讯系统最大的区别。

---

# 62. 推荐开发结论

最终建议如下：

1. **不要重新开发一个独立新项目。**
2. **复用 `daily_industry_briefing` 的运行、存储、展示、部署体系。**
3. **以 `codex/opportunity-tab-v1` 为技术基础。**
4. **保留规则模块作为候选粗筛和 AI 失败时 fallback。**
5. **新增可插拔 LLM Provider；OpenAI Responses API + Web Search 可作为首个基准实现，同时支持 GLM 等 Provider。**
6. **新增 Company Fit，让排序真正符合公司现阶段业务。**
7. **增强 Project Memory，解决跨日重复与阶段变化。**
8. **新增独立企业微信 Publisher，使用已配置的 `WECOM_OPPORTUNITY_WEBHOOK_URL`。**
9. **先运行 2~4 周与 ChatGPT 日报对照，再完全自动化。**
10. **目标不是每天硬凑5条，而是稳定提供真正值得销售跟进的高质量商机。**

---

# 63. Codex 实施任务摘要

Codex 最终应交付：

```text
[ ] 保持原四个资讯Tab完全不变
[ ] 商机模块独立运行
[ ] LLM Provider抽象层
[ ] OpenAI Provider接入
[ ] GLM Provider预留/接入
[ ] Search Provider抽象层
[ ] Web Search接入
[ ] Structured Outputs
[ ] AI Discovery
[ ] AI Research
[ ] 官方来源验证
[ ] Evidence
[ ] Company Profile
[ ] Company Fit
[ ] Project Memory增强
[ ] Top 5 Ranking
[ ] 商机JSON
[ ] 商机Tab
[ ] 企业微信Webhook
[ ] 推送测试接口
[ ] API状态
[ ] AI失败fallback
[ ] Webhook失败不影响主任务
[ ] 日志与usage
[ ] pytest
[ ] live test脚本
[ ] runbook
```

---


## 多模型补充说明

智谱 BigModel/GLM 具备独立 API 与智能体能力，其官方文档中也提供了带 `web_search` 工具的智能体能力。因此 GLM 可以作为本项目的候选模型供应商。

但是，不同供应商的：

```text
联网搜索
工具调用
结构化输出
上下文长度
计费
速率限制
引用返回格式
```

并不完全一致。

因此 V2 文档不应再把“OpenAI API”写成不可替换的底层依赖，而应把它定义为：

> **首个 Provider / 质量基准实现之一。**

商机业务层只依赖统一的 Provider 接口。


# 64. 官方技术参考

规划采用以下 OpenAI 官方能力：

- Responses API
- Web Search tool
- Structured Outputs
- `max_tool_calls`
- 可配置 reasoning effort

OpenAI 当前官方文档说明，Responses API 可直接启用 `web_search`，推理模型可自行决定继续检索；Structured Outputs 可用 JSON Schema 约束返回结构；Responses API 还支持 `max_tool_calls` 限制一次响应中内置工具调用数量。

建议开发时以最新官方文档为准，不将模型名、价格和能力假设长期写死在业务逻辑中。


# 65. 前端与分发的最终验收要求

V2 上线前必须同时满足以下三项：

1. **同一份 OpenAI API 商机结果同时提供给“商机”Tab和企业微信。**
2. **“商机”Tab 的整体前端风格必须与国内、本地、公众号、发现 Tab 完全一致，只允许中间业务字段不同。**
3. **企业微信可以采用独立的聊天消息排版，但必须来自同一份最终商机数据，并带有进入“商机”Tab 的链接。**

最终数据链必须是：

```text
OpenAI API
    ↓
ProjectOpportunity[]
    ↓
output/opportunities/YYYY-MM-DD.json
    ↓
┌─────────────────────────┬─────────────────────────┐
↓                         ↓
资讯杂志商机Tab             企业微信商机日报
完整阅读                    摘要提醒
```

如果出现“网页和企微内容不同源”“商机 Tab 另做一套界面”“企业微信再次调用 AI 生成另一份内容”，均视为不符合 V2 设计要求。
