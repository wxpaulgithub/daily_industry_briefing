# 商机模块 V1 实施说明

## 目标

商机模块只回答“有哪些项目值得跟进？”，不复用国内资讯页的排序和生命周期。V1 采用规则系统，不引入 LLM、数据库、Redis 或 Celery。

运行、调试、人工刷新和故障排查见 [`OPPORTUNITY_RUNBOOK.md`](OPPORTUNITY_RUNBOOK.md)。

## 管线

```text
独立公告/搜索来源
→ 候选发现
→ 仓储相关性与硬排除
→ 规则字段提取
→ 项目类型和阶段
→ 商机评分
→ 同日/跨日去重
→ 每日快照
```

项目类型为 `NEW_BUILD / RETROFIT / MAINTENANCE / SOFTWARE / EQUIPMENT / MIXED / UNKNOWN`；阶段为 `EARLY_SIGNAL / PROCUREMENT / AWARD / CLOSED / UNKNOWN`。页面优先展示采购期项目，并保留早期信号；关闭项目不展示。

## 数据与接口

- 配置：`config_data/opportunity_queries.json`
- 每日快照：`output/opportunities/YYYY-MM-DD.json`
- 跨日索引：`output/opportunities/index.json`
- 页面：`/?scope=opportunity`
- 今日 API：`GET /api/opportunities/today`
- 历史 API：`GET /api/opportunities/{date}`
- 刷新：`POST /api/refresh?scope=opportunity`
- 定时：每日 07:20，独立于 07:00 资讯任务

商机页复用现有 `templates/magazine.html` 的完整页面壳层和交互，只替换中间内容字段；页头、主标题、背景主题、Tab 导航、页脚和刷新按钮与其他资讯范围保持一致。

搜索结果只用于发现。`source_score` 明确优先政府采购、公共资源和招投标官方来源。预算、截止时间、业主和地区均从已有文本按规则抽取，不生成事实。

## 评分

```text
final_score = relevance × 0.50 + urgency × 0.30 + source × 0.20
```

相关性要求同时考虑仓储技术词和项目意图词；运输、配送、租赁、保洁等假阳性使用硬排除。分数只用于排序，前端显示为“优先度”，不将其表述为事实置信度。

## 验收

- 黄金样本覆盖高相关、早期信号、中标、关闭和运输类误报。
- 同一项目的“采购公告/招标公告/中标公告”生成同一 `project_key`。
- 跨日更新保留 `first_seen_at`，刷新 `last_seen_at`。
- 商机刷新不改写资讯 JSON，资讯四个 scope 保持原逻辑。
- 抽查 Top 结果的官方链接、阶段、预算和截止时间；原文无字段时保持为空。
