# 智能仓储行业情报平台总体规划

## 产品定位

项目由“新闻聚合器”逐步升级为轻量级智能仓储行业情报站。六个一级入口分别回答不同问题：

| 模块 | 核心问题 | 更新频率 | 生命周期 |
| --- | --- | --- | --- |
| 商机 | 有哪些项目值得跟进？ | 每日 | 项目可持续数月 |
| 展讯 | 未来有哪些活动值得去？ | 每周 | 事件持续至举办 |
| 国内 | 行业最近发生了什么？ | 每日 | 一天一批 |
| 本地 | 无锡及周边有什么潜在线索？ | 每日/数日 | 中强时效 |
| 公众号 | 垂直媒体和行业账号在讨论什么？ | 每日 | 阅读型 |
| 发现 | 外围渠道有什么值得看的内容？ | 每日 | 探索型 |

最终导航为 `国内 | 商机 | 展讯 | 本地 | 公众号 | 发现`，但产品更名和整体品牌调整应等商机与展讯稳定后再做。

## 架构原则

资讯、商机和展讯使用独立管线。HTTP、正文提取、日期/地区解析、URL 规范化、图片和日志可以共享；评分、分类、去重和存储生命周期分别实现。V1 不建设通用 intelligence framework，先以 `services/opportunity/` 和后续 `services/events/` 跑通真实业务。

```text
新闻源 → Existing Pipeline → 国内/本地/公众号/发现
项目源 → Opportunity Pipeline → 商机
展会源 → Event Pipeline → 展讯
```

## 商机

商机是每日快照，保存为 `output/opportunities/YYYY-MM-DD.json`。核心流程、实体、规则、接口与验收详见 `docs/OPPORTUNITY_TAB_V1.md`。

## 展讯（商机验收后实施）

展讯维护长期存在的 `IndustryEvent`，不是 Article，也不按天重复生成。主要存储为 `output/events/upcoming.json`，必要时按 `status` 归档。

实体至少包含名称、官方/发现 URL、主办方、日期、城市、省份、展馆、类型、相关性、商业价值、价值类型、标签、状态、报名截止、参展商/议程 URL、图片、首次发现/检查/变化时间和 `event_key`。

活动类型重点覆盖 `EXHIBITION / CONFERENCE / FORUM`；价值类型支持 `CUSTOMER_DISCOVERY / PARTNER_DISCOVERY / COMPETITOR_INTELLIGENCE / BRAND_EXPOSURE / LEARNING`。状态支持 `ANNOUNCED / REGISTRATION_OPEN / UPCOMING / ONGOING / ENDED / CANCELLED / POSTPONED`。

来源优先级为展会官网、主办方、协会/展馆、权威行业媒体、搜索发现。搜索只发现线索，不默认充当官方链接。评分建议：

```text
final_score = relevance × 0.35 + business_value × 0.45 + source × 0.20
```

页面按未来 30 天、31–90 天、90 天以后分组；前两组按价值排序，远期按日期排序。定时任务每周一 08:00 执行，页面访问只读已有结果。

展讯 V1 应增加 `GET /api/events/upcoming`、`GET /api/events/all`、`scope=events` 刷新、状态统计、变化检测、去重/日期/评分/存储/路由测试及黄金样本。禁止引入 LLM、数据库、Redis 和 Celery。

## 实施顺序

1. 在 `codex/opportunity-tab-v1` 完成商机管线并用真实结果与 ChatGPT 日报对比。
2. 保留 2–4 周基准，记录漏报、误报、来源缺失和阶段误判；反馈原因包括 `not_warehouse / already_closed / too_old / duplicate / transport_service / pure_industry_news / low_fit`。
3. 商机验收并合并后，从最新主分支创建 `codex/events-tab-v1`，不修改已上线商机业务逻辑。
4. 核对未来 180 天展会后再合并展讯。

长期数据库应保留 `Article / ProjectOpportunity / IndustryEvent` 三类实体。AI 只在规则稳定后用于摘要、分类和判断；日期、预算、开标时间和展会地点等事实字段始终以原文为准。
