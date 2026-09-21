# 商机 V1 运行与调试手册

本手册对应 `codex/opportunity-tab-v1`。它只覆盖商机管线，展讯仍按 `docs/INTELLIGENCE_PLATFORM_PLAN.md` 中的后续 PR 处理。

## 1. 本地启动

在项目目录执行：

```powershell
.\venv\Scripts\Activate.ps1
uvicorn app:app --host 127.0.0.1 --port 8088 --reload
```

打开：

```text
http://127.0.0.1:8088/?scope=opportunity
```

商机页不会加载独立视觉模板，而是复用现有资讯页 `templates/magazine.html` 的页头、主题、背景、导航、页脚和刷新交互；中间卡片只改为项目阶段、类型、优先度等商机字段。

服务启动时会注册两个互不阻塞的每日任务：资讯 `07:00`，商机 `07:20`。也可以直接运行 `python app.py`，但开发调试推荐使用上面的 uvicorn 命令。

## 2. 不启动 Web 服务直接采集

```powershell
# 只跑资讯，保持原行为
.\venv\Scripts\python.exe fetch.py

# 只跑商机并写入当天快照
.\venv\Scripts\python.exe fetch.py --scope opportunity
```

商机结果写入 `output/opportunities/YYYY-MM-DD.json`，跨日索引写入 `output/opportunities/index.json`。`output/` 已被 Git 忽略，不应提交运行产物。

## 3. 手动刷新与查看

PowerShell：

```powershell
Invoke-RestMethod -Method Post 'http://127.0.0.1:8088/api/refresh?scope=opportunity'
Invoke-RestMethod 'http://127.0.0.1:8088/api/opportunities/today' | ConvertTo-Json -Depth 8
Invoke-RestMethod 'http://127.0.0.1:8088/api/status' | ConvertTo-Json -Depth 8
```

刷新接口是异步的。先得到 `started`，再轮询 `/api/status`，直到 `opportunity.fetching` 为 `false`。页面的“刷新商机”按钮执行同样的流程。

## 4. 配置来源和规则

编辑 `config_data/opportunity_queries.json`：

- `official_sources`：官方公告列表，优先级最高；
- `queries`：搜索发现词，只用于发现候选，不默认视为官方来源。

规则代码位于 `services/opportunity/`：

| 文件 | 用途 |
| --- | --- |
| `models.py` | `OpportunityCandidate`、`ProjectOpportunity` 和稳定键 |
| `rules.py` | 仓储相关性、硬排除、编辑型内容排除、类型/阶段判定、过期判断 |
| `enrichment.py` | 业主、预算、截止时间、省市的规则抽取 |
| `scoring.py` | 相关性、紧迫度、来源可信度和最终分数 |
| `fetcher.py` | 来源并行抓取、正文 enrichment、筛选和去重 |
| `storage.py` | 每日快照和跨日索引 |

修改关键词后先补 `tests/fixtures/opportunity_cases.json`，再运行测试，避免只凭当天网络结果调整规则。

## 5. 测试和静态检查

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
$files = @('app.py','config.py') + (Get-ChildItem services\opportunity -Recurse -Filter *.py | ForEach-Object FullName)
& .\venv\Scripts\python.exe -m py_compile @files
git diff --check
```

黄金样本覆盖高相关、采购、早期信号、中标、关闭和运输/配送误报。跨日去重测试会验证同一项目保留原始 `first_seen_at`。

## 6. 常见故障

### 官方网站 403 或“频率过高”

这类站点有反爬和 IP 频控。管线会记录 `[Opportunity/official_bidding]` 警告，继续使用搜索发现源，不会让整个任务失败。不要在短时间内反复点刷新；间隔数分钟后再试，或临时减少 `official_sources`。

中国政府采购网部分页面响应头误报编码，适配器已对 `ccgp.gov.cn` 尝试 GB18030 解码。若页面结构改变，优先检查 `services/opportunity/sources/bidding.py` 的 `_decode_html` 与 `_extract_links`。

### 采集成功但数量为 0

按顺序检查：

1. `config_data/opportunity_queries.json` 是否有有效 URL/查询词；
2. 日志中的 `source candidates`、`detail enriched`、`relevant` 数量；
3. 候选是否被 `HARD_EXCLUDES`、`EDITORIAL_EXCLUDES` 或阶段/过期阈值过滤；
4. 用 `python -m unittest discover -s tests -v` 确认不是规则回归。

### 结果看起来像行业文章，不像项目

在 `rules.py` 增加可复现的排除词，并在黄金样本中添加该标题。不要只调低分数：采购前指南、排行榜、品牌对比等内容应直接排除；真正项目必须有采购、招标、中标或建设/立项信号。

### 页面显示旧数据

API 和页面按当天日期读取快照。检查 `output/opportunities/` 下文件的修改时间和 `index.json`；服务重启后 `/api/status` 的 `last_run` 只表示本次进程内运行记录，不代表历史快照不存在。

## 7. 人工反馈

`config_data/opportunity_feedback.json` 预留人工标注结构，当前不影响管线：

```json
{
  "decisions": [
    {"project_key": "...", "decision": "irrelevant", "reason": "transport_service"}
  ]
}
```

建议先积累 2–4 周样本，再调整规则。常用原因：`not_warehouse`、`already_closed`、`too_old`、`duplicate`、`transport_service`、`pure_industry_news`、`low_fit`。

## 8. 生产排查顺序

```text
/api/status
  → 查看 opportunity.fetching / last_run / count
output/opportunities/YYYY-MM-DD.json
  → 核对快照和字段来源
日志 [Opportunity]
  → candidates → enriched → relevant → deduplicated
黄金样本测试
  → 判断是否规则回归
```
