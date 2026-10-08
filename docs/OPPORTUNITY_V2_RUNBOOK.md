# AI 商机 V2 运行与调试手册

当前实施分支：`codex/opportunity-tab-v1`。设计见 `AI_商机日报_V2_技术实施规划.md`；V1 两份文档保留为规则基线记录。

## 1. 配置和启动

首次复制 `.env.example` 到 `.env`；已有 `.env` 时直接修改，不覆盖。填写所选供应商的密钥和商机模型名，再启动：

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\Activate.ps1
uvicorn app:app --host 127.0.0.1 --port 8088 --reload
```

本地 `.env` 由 `python-dotenv` 读取，进程环境变量优先；Docker Compose 会注入相同配置。模型名不设置硬编码默认值，密钥或模型缺失时使用规则回退。

| 配置 | 用途/默认值 |
| --- | --- |
| `LLM_PROVIDER` | `openai` 或 `glm`，默认 openai |
| `OPENAI_API_KEY` / `OPENAI_OPPORTUNITY_MODEL` | OpenAI 凭据和可配置模型 |
| `GLM_API_KEY` / `GLM_OPPORTUNITY_MODEL` | GLM 凭据和可配置模型 |
| `OPPORTUNITY_AI_ENABLED` | true；false 时使用规则 |
| `OPPORTUNITY_AUTO_PUBLISH` | false，灰度期只生成和保存 |
| `OPPORTUNITY_FALLBACK_PROVIDER` | 可选第二供应商，未配置则回退规则 |
| `OPPORTUNITY_SEARCH_PROVIDER` | `auto`、`existing`、`openai` 或 `glm`；指定 `glm` 时使用智谱 `web-search-pro` 搜索 |
| `GLM_WEB_SEARCH_ENABLED` | 默认 false；设为 true 后，GLM 分析模型搭配智谱 `web-search-pro` 实际联网搜索 |
| `OPPORTUNITY_AI_REASONING` | medium；不支持 reasoning 的 OpenAI 模型可设为空 |
| `OPPORTUNITY_AI_RESEARCH_LIMIT` | 12 个深度研究项目 |
| `OPPORTUNITY_CANDIDATE_LIMIT` | 40 个初筛候选，为弱关键词项目预留名额 |
| `OPPORTUNITY_DISPLAY_LIMIT` / `OPPORTUNITY_DIGEST_LIMIT` | 网页10条、企微最多5条，不凑数 |
| `OPPORTUNITY_TASK_TIMEOUT` | 1200秒 |
| `OPPORTUNITY_DISCOVERY_SEARCH_BUDGET` | 8，Discovery及扩展搜索预算 |
| `OPPORTUNITY_VERIFY_SEARCH_BUDGET` | 12，官方源检索预算 |
| `OPPORTUNITY_TOTAL_SEARCH_BUDGET` | 24，全任务硬上限；未配置时兼容旧MAX_TOOL_CALLS |
| `OPENAI_OPPORTUNITY_MAX_TOOL_CALLS` | 旧配置兼容；新总预算显式配置优先 |
| `OPPORTUNITY_MEMORY_RECHECK_HOURS` | 24，内容一致的已核验历史结果可复用的最长间隔 |
| `OPPORTUNITY_MAX_LLM_CALLS` | 40 次模型尝试，含重试 |
| `WECOM_OPPORTUNITY_WEBHOOK_URL` | 独立商机机器人，不复用 RSS 告警 Webhook |
| `SITE_URL` | 完整日报入口的公开站点地址 |

OpenAI Provider 采用 Responses REST API + `web_search` + strict JSON Schema；GLM 分两步：先用智谱官方 `web-search-pro` 搜索模型拿到网页标题、链接和摘要，再交给 `GLM_OPPORTUNITY_MODEL` 阅读可访问的公告原文并按 Pydantic Schema 分析。两者共用 `GLM_API_KEY` 和智谱 API 地址，不需要另装供应商 SDK。使用 GLM 时可在 `.env` 设置 `GLM_WEB_SEARCH_ENABLED=true`；旧配置 `GLM_NATIVE_WEB_SEARCH` 仍兼容。也可显式设 `OPPORTUNITY_SEARCH_PROVIDER=glm` 强制用智谱搜索。搜索结果只是线索，仍需成功读取原文并通过证据核验才会成为正式商机。参考[智谱 Web-Search-Pro 示例](https://docs.bigmodel.cn/cn/best-practice/case/ai-search-engine)。

公司档案在 `config_data/company_profile.json`，按 V2 规划初始化。上线前按实际能力、预算区间、服务地区调整。公告来源和查询在 `opportunity_queries.json`；可增加 `trusted_domains`（企业官网/正式采购平台）及 `authoritative_domains`（协会、权威机构）域名白名单。可信度依据实际 hostname，不依据“官网”字样。

## 2. 页面和任务

打开 `http://127.0.0.1:8088/?scope=opportunity`。页面只读快照，不触发收费请求；无数据时使用“刷新商机”或研究接口。商机复用 `magazine.html` 的页头、主题、背景、导航、页脚、头条和卡片，内容增加优先级、阶段、预算、技术范围、关注理由、切入点和可展开证据。

资讯任务 `07:00`，商机任务 `07:20`（Asia/Shanghai），互不阻塞。历史页面为 `/archive/YYYY-MM-DD?scope=opportunity`。独立采集使用同一保存/推送逻辑：

```powershell
.\venv\Scripts\python.exe fetch.py --scope opportunity
```

是否自动推送遵循 `OPPORTUNITY_AUTO_PUBLISH`。

## 3. 接口

| 接口 | 方法 | 作用 |
| --- | --- | --- |
| `/api/opportunities/today` | GET | 今日快照 |
| `/api/opportunities/YYYY-MM-DD` | GET | 指定日期快照 |
| `/api/opportunities/status` | GET | 持久化状态、计数和发布状态，无凭据 |
| `/api/refresh?scope=opportunity` | POST | 研究、保存，按配置自动发布 |
| `/api/opportunities/research` | POST | 同上 |
| `/api/opportunities/publish-test` | POST | 只发“推送通道测试成功”，不含真实商机 |
| `/api/opportunities/publish-today` | POST | 从今日已保存快照发布新增或变化项目 |

```powershell
Invoke-RestMethod -Method Post 'http://127.0.0.1:8088/api/opportunities/research'
Invoke-RestMethod 'http://127.0.0.1:8088/api/opportunities/status' | ConvertTo-Json -Depth 8
```

研究立即返回 `started`，轮询至 `fetching=false` 且 `stage=DONE`。正常零结果mode=ai、fallback_reason为空，即使当天有缓存仍保存空数组；只有真实故障才考虑规则或缓存回退。

## 4. 事实、评分和项目记忆

关键事实必须对应已获取HTML/PDF原文中的短引用。HTML可发现最多2个直接PDF附件；PDF最多10MB/80页，独立进程解析15秒超时，单来源总获取30秒上限，不做OCR。证据保存PDF页码；扫描件/过大/截断明确标记风险。仅忽略空白和英文大小写匹配，不能模糊补写。不同项目的来源不能混用。

真实GLM/Existing工具摘要可形成preview；OpenAI模型自行生成的summary不能充当搜索证据。OpenAI实际sources进入验证链，只有URL时继续打开原文。verification_status与运行降级分开保存，旧摘要风险标记也能阻止发布；超时/缓存/重试均不能将摘要提升为fallback可发布项目。

无法核验的业主、地区、发布日期、预算、截止时间留空并记录风险；原文证据不匹配时不使用模型猜测。URL、日期、预算数值、项目键和截止比较由程序计算。截止时间保留时分；只有日期时保留至当天结束。无日期/截止时间的项目最高为“观察”，未来异常日期、过期、已中标、关闭项目不进入销售日报。

评分为：相关性30% + 阶段20% + 新鲜度15% + 来源10% + 公司适配25%。分数用于排序，界面显示“重点关注 / 值得跟进 / 观察”。

Discovery读取最近14天最多50条历史。研究名额分配前，只有当前已读取内容签名完全一致、历史已核验且未超过复核间隔才复用；未知变化、首次无签名、preview恢复原文均研究。带附件或截断原文不跳过。二次招标/澄清公告版本参与变化签名。

前置采集保留官方列表，限定普通检索为一个query的两次请求并纳入Discovery，旧资讯候选从最近三个已保存资讯JSON复用，避免重跑隐藏的嵌套搜索；普通资讯采集逻辑不变。

项目索引保存首次/最后发现、当前及先前阶段/预算/截止、最后推送时间与内容签名。中标/关闭观察也更新索引，较旧采购页面不能重新打开项目。新增/变化项目排在快照前部，页面和企微使用相同顺序；无变化项目留在页面后部，推送时跳过。进入采购期、预算/截止或技术范围变化时可再次提醒。只有企微 `errcode=0` 才更新推送记忆，独立持久化回执防止成功发送后文件更新失败造成重复。

## 5. 保存、分发和灰度

顺序：研究 → 原子保存每日 JSON → 更新索引 → 页面可读 → 企微摘要。发布模块只消费快照，不调用 AI，标题、预算、阶段和切入点来自网页相同数据。HTTP 日志脱敏 query 中的 key/token，API 不返回凭据。

失败不撤销快照。默认10分钟重试，服务每分钟检查持久化任务，总共最多3次尝试。跨日任务、内容已经变化的快照和耗尽重试的任务不会继续重发。Webhook 超时无法确认远端是否接收，人工核对群消息后再手动重试。

先保持 `OPPORTUNITY_AUTO_PUBLISH=false`，连续2–4周对照现有 ChatGPT 日报、抽查来源和事实，再开启自动推送。`publish-test` / `publish-today` 会向配置的群发消息，应在需要发送时调用。

## 6. 状态、用量和部署

| 文件 | 内容 |
| --- | --- |
| `output/opportunities/YYYY-MM-DD.json` | 网页/企微同源商机快照 |
| `output/opportunities/index.json` | 跨日项目记忆，包含未进入销售日报的阶段变化 |
| `runtime/opportunity_ai_status.json` | DISCOVERING → PREFILTERING → RESEARCHING → VERIFYING → RANKING → SAVING → PUBLISHING → DONE / FAILED |
| `runtime/opportunity_publish_status.json` | 尝试次数、回执、下次重试时间 |
| `runtime/opportunity_usage.json` | 每日模型尝试、搜索次数、Token 和分供应商用量 |

Docker 使用持久卷 `/runtime`，由 `OPPORTUNITY_RUNTIME_DIR` 指定。JSON 使用临时文件+原子替换，避免半写内容。费用单价不硬编码；填写 `.env.example` 的 Token/搜索单价后才估算，多供应商用量无法使用统一单价估算时为 null。

```powershell
docker compose -p industry_briefing --env-file .env config --quiet
```

Dockerfile 先启动 Web 服务，由 lifespan 异步执行原有启动资讯采集，图片下载在线程中执行；启动过程可响应健康检查。重启不会额外触发收费 AI 研究。镜像包含配置预检脚本和 live 联调脚本，构建上下文排除 `.env*`、runtime、output 和测试数据。

自动部署工作流先运行离线回归、编译、Compose、镜像构建和容器内配置预检。当前开发分支 push、面向 master 的 PR 以及手动运行都可验证；校验阶段增加无外网、无生产卷的独立容器健康检查；只有 master 才执行服务器部署，并部署该次已验证的提交。服务器 `.env` 仍位于 `/opt/industry_briefing/.env`。

服务器先构建镜像并预检配置，再替换容器；开启自动推送却缺少独立商机 Webhook 时预检失败。缺少 AI 密钥/模型且自动推送关闭时可采用规则回退。预检只输出配置状态，不打印凭据，也不调用模型或机器人。上线后同时检查 `/api/status`、`/api/opportunities/status`、`/api/opportunities/today`。

```powershell
.\venv\Scripts\python.exe scripts/check_opportunity_config.py
```

## 7. 离线与真实验收

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m compileall -q services tests scripts app.py fetch.py
git diff --check
```

自动测试全部 mock，不调用收费 API，不发真实消息。Windows 受限沙箱可能阻止 asyncio 创建回环 socket，应在允许本地连接的执行环境测试。

`tests/fixtures/opportunity_gold.json` 有96条明确标记的模拟回归场景，验证规则和过滤，不代表线上90%准确率。真实验收需要收集80–100条公告并人工标注，连续2–4周记录漏报、误报、排名、阶段、预算和重复问题。

真实数据入口见 `docs/opportunity_real_benchmark.template.json`（空模板不算真实数据）和 `docs/OPPORTUNITY_REVIEW2_BENCHMARK.md`。首次30–50条人工核验，之后80–100条；数据/金标与工具交付分开验收。

现有脚本支持三个模式：research固定候选和冻结原文、discovery独立联网、longitudinal跨日回放。研究Recall是候选保留率；发现Recall只是标注池覆盖下界。preview、规则回退和核验结果分开，指标含事实完整率及分母。

```powershell
# 不调用模型：人工标注后取原文归档，也可提供已冻结documents省略fetch-sources
.\venv\Scripts\python.exe scripts/test_opportunity_ai_live.py --prepare --fetch-sources --candidates runtime/real_opportunity_cases.json
# 明确收费：固定原文按evaluation_date比较，不写正式快照、不发企微
.\venv\Scripts\python.exe scripts/test_opportunity_ai_live.py --live --mode research --compare --candidates runtime/real_opportunity_cases-frozen.json
# 明确收费：当天独立联网发现，已标注候选作为评测池，使用当前日期
.\venv\Scripts\python.exe scripts/test_opportunity_ai_live.py --live --mode discovery --compare --candidates runtime/today_labeled_cases.json
# 无收费：对保存的结果数组计算指标，或回放跨日快照
.\venv\Scripts\python.exe scripts/test_opportunity_ai_live.py --mode research --candidates runtime/real_opportunity_cases-frozen.json --results runtime/items.json
.\venv\Scripts\python.exe scripts/test_opportunity_ai_live.py --mode longitudinal --snapshots output/opportunities
```

模板字段及原文格式见Benchmark说明。预算按金额单位归一化，URL追踪参数/别名和项目身份匹配。失败/未知消耗保守扣预算，已知成功释放未用预留；GLM请求和OpenAI工具动作计量不等同费用。当前不声称真实质量达标，自动推送维持false；2–4周及至少100条人工裁决并达门槛后另行决定。

## 8. 排查与反馈

先查状态，再核对快照、索引及 `[OpportunityAI]` / `[OpportunityPublisher]` 日志。

- `provider_not_configured`：确认所选供应商 Key/模型名已进入容器。
- `provider_http_401/403`：检查账户/API权限。
- `invalid_structured_output`：Schema 校验失败，重试一次后降级。
- `search_failed=true`：仍可分析现有候选，核对搜索源或原生搜索兼容性。
- `published_date_not_verified` / `unsupported_budget`：检查证据页面和逐字引用，事实缺失留空。
- 发布失败：检查群机器人与重试状态；生成成功、发布成功分开判断。

反馈兼容原有 `{"decisions": [...]}` 和数组。`reject` / `irrelevant` 按项目键确定性排除，最近50条反馈进入研究上下文，当前不训练模型。

接口依据：[OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)、[GLM API 文档](https://docs.bigmodel.cn/llms.txt)。


## 8. 真实质量排查

`last_researched`是本轮计划数，`last_research_completed`是已完成数；每项结束更新调用量。`runtime/opportunity_research_diagnostics.json`记录发现候选、研究名额、语义筛除/来源不足/Provider错误、结果风险及最终是否保留。来源失败与验证码页面有独立原因，HTTP200不等于有效公告原文。

比较GPT日报时同时核实原文报名窗口和参与资格，不只看投标截止。2026-10-08实例及改动见OPPORTUNITY_QUALITY_ITERATION_2026-10-08.md。修改Python后重启本地服务再运行，历史快照保持历史记录，下一次刷新才生成新结果。


## 9. 第三轮召回流程及调试

默认流程为八个全国固定搜索通道 → 根据真实结果补搜最多两次 → 硬排除与多样候选池60条 → 一次不联网的Semantic Triage → 六条一批研究、软12/硬18 → 本轮取得五条核验通过、时效可用且等级A/B的去重项目时停止。已复用历史项目与preview均不占用停止目标。页面仍可展示明确标记的待核验线索；preview仍不得推送。

默认发现/验证/总搜索预算为12/24/36，模型调用上限64（含搜索与重试）；超时、队列耗尽或预算耗尽均可提前停止，不保证五条。OPPORTUNITY_AI_RESEARCH_LIMIT现在是软边界，真正数量上限是OPPORTUNITY_RESEARCH_HARD_LIMIT；波次大小和目标分别使用OPPORTUNITY_RESEARCH_WAVE_SIZE、OPPORTUNITY_VERIFIED_TARGET。

本地.env已仅更新上述非秘密预算及候选参数；密钥、Provider和自动推送配置保留。服务进程需要重启才会使用新代码和环境值。云端.env若已有旧预算，会覆盖Compose默认值，部署前同步这些参数。

1. 先运行python scripts/check_opportunity_config.py，查看limits与warnings；结果不打印密钥。GLM/OpenAI八通道至少需要10次发现预算（其中2次供既有搜索）；默认12留2次补搜。ExistingSearch每个查询使用两次HTTP搜索，完整八通道及两次补搜需发现预算22，验证18个候选基础预算36；总预算须另行配置，不会自动扩大上限。
2. 重启本地服务，再由商机刷新触发完整运行。先看是否进入TRIAGING、是否分批研究；不要把绿色HTTP200或测试通过理解为召回达标。
3. runtime/opportunity_research_diagnostics.json包含discovery_coverage各方向请求/返回/预算/异常及进入候选池、分流、研究、保留的数量；returned是该方向返回线索，不是已核验商机数量。零结果仅表示此次未找到，可用剩余额度做专项补搜，不能断言市场没有项目。
4. pipeline与drop_reason_counts包含全部去重候选的去向；candidates包含已研究项目的source_resolution和保留情况。历史年份标题无可信新发布日期/有效未来截止，不进入最终推荐；重新招标字样只允许继续核验，不证明时效。
5. 高分候选首次取不到相关原文时，Source Resolver最多追加两种查询，总共不超过3次。原URL始终先尝试；业主设备、已知官方域名、地区设备等按已知线索选择；额外查询只使用扣除后续基础查找额度后的余额。找到了另一个页面仍需通过原有项目关联及逐字证据核验，不绕过验证码或登录，不启用浏览器采集。
6. python scripts/test_opportunity_ai_live.py的--provider选择分析模型，--search-provider可独立选择auto/openai/glm/existing。research模式对冻结原文做分析，不运行Triage或搜索；discovery模式报告pipeline_metrics各人工标注目标在哪一步未进入结果。只有显式--live才调用真实模型；本轮未调用收费API。


### 429与搜索失败观察

OPPORTUNITY_SEARCH_CONCURRENCY默认1；八个通道排队执行，GLM原文查找复用此限制。discovery_status为failed时不能把候选总数理解为联网搜索成功；partial表示至少一个通道因请求失败或预算不足未完成。持续输出OpportunityDiscovery日志显示通道、返回量和稳定错误码。429具体原因需结合服务商error.code/控制台判断，不能仅凭HTTP状态断言是并发。修改代码后需要重启再由用户触发验证；不会自动补发收费请求。
