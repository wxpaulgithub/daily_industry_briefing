# AI商机V2第二轮实施记录

日期：2026-10-08。分支：codex/opportunity-tab-v1。基线：0a74a1d。代码和任务书已本地修改，没有commit/push，没有merge master，没有开启自动企微。

## 本轮实现

- P0：正常零结果mode=ai，不恢复当天旧缓存；故障缓存保持独立证据等级；旧摘要风险标记也阻止发布。OpenAI模型summary不再当工具摘要；有实际工具摘要才可preview；无原文搜索项不能转规则fallback。
- Memory：Discovery包含最近14天最多50项历史；在40项候选截断之前复用已读取内容完全一致、历史已核验且未过24小时复核间隔的结果，重新计算新鲜度/适配；未知变化仍研究，未推送旧项目仍可保留。二次招标/澄清版本进入material_hash，旧版本不能反复覆盖新版本。
- Search：Discovery/Verification/Total分阶段预算8/12/24；成功释放未用预留，失败/取消未知消耗保守扣；状态/usage记录阶段实际调用、请求和未知消耗。Existing搜索计量，前置一个query最多两次请求，旧资讯复用本地快照，不重新跑隐藏搜索。旧MAX_TOOL_CALLS在新总预算未显式配置时兼容。
- 来源：OpenAI返回真实sources并逐响应隔离，不使用共享last_sources；GLM保留真实snippet、origin及时间；不同项目证据不能混用，Company Fit只读取同项目证据来源。
- PDF：10MB/80页、字符上限、独立解析进程15秒超时、来源获取30秒；HTML最多2个直接PDF附件；不做OCR，页码/提取失败/截断风险可追溯。仅支持直接PDF链接，动态下载/登录附件留待真实灰度评估。
- 过滤：否定表达“不含运输服务”等不会触发硬排除，纯运输负样本仍过滤。
- Benchmark：research冻结候选与原文并按evaluation_date运行；discovery只报告标注池覆盖；longitudinal分开网页重复出现与确认投递重复。项目身份/URL别名匹配、事实完整率/分母、错误清单、preview和fallback分列。各Provider候选隔离，无正式快照写入和企微调用。
- 域名：既有白名单接口补齐配置和规范化/维护说明；尚无真实项目确认的企业域名保持空列表，没有猜测域名。
- CI：保留当前分支触发范围，增加无外网、无生产卷的镜像健康检查。共享杂志视觉只新增PDF证据页码，普通资讯四个Tab未重写。

## 验证

- 101个离线测试全部通过（原66项+35项新增）；96条synthetic粗筛/阶段回归保持。所有Provider/Webhook网络测试使用mock。
- Python compileall、git diff --check、Compose .env.example配置检查通过。
- industry-briefing:review2镜像构建通过；镜像内配置预检ok=true，API/机器人均未配置，auto_publish=false。
- 最终镜像在--network none独立容器验证/api/status、/api/opportunities/status、/api/opportunities/today，均200；没有生产卷/对外端口，容器已移除。
- 远端CI尚未运行这些工作区修改，不能声称CI已绿。基线CI失败状态未改变。

## 未完成的真实验证

尚无30–50条人工金标、企业采购域名确认、OpenAI/GLM真实API评测和2–4周灰度。空模板不是数据集；自动测试不是线上准确率。P2 engagement_mode和更多动态搜索表达未列入本轮阻塞项。

建议下一步先整理真实金标并归档原文，再显式--live比较。需配置OPENAI_API_KEY/OPENAI_OPPORTUNITY_MODEL及GLM_API_KEY/GLM_OPPORTUNITY_MODEL；GLM发现使用GLM_WEB_SEARCH_ENABLED=true。全部评测保持OPPORTUNITY_AUTO_PUBLISH=false，不需真实Webhook。命令及数据字段见OPPORTUNITY_V2_RUNBOOK.md与OPPORTUNITY_REVIEW2_BENCHMARK.md。

## 修改文件

- ["docs/\344\277\256\350\256\242\344\273\273\345\212\241\344\271\246_AI\345\225\206\346\234\272V2_\347\254\254\344\272\214\350\275\256\345\256\241\346\237\245"](I:/资讯杂志/"docs/344/277/256/350/256/242/344/273/273/345/212/241/344/271/246_AI/345/225/206/346/234/272V2_/347/254/254/344/272/214/350/275/256/345/256/241/346/237/245")
- [.env.example](I:/资讯杂志/.env.example)
- [.github/workflows/deploy.yml](I:/资讯杂志/.github/workflows/deploy.yml)
- [config_data/opportunity_queries.json](I:/资讯杂志/config_data/opportunity_queries.json)
- [docker-compose.yml](I:/资讯杂志/docker-compose.yml)
- [docs/OPPORTUNITY_REVIEW2_BENCHMARK.md](I:/资讯杂志/docs/OPPORTUNITY_REVIEW2_BENCHMARK.md)
- [docs/OPPORTUNITY_SOURCE_DOMAINS.md](I:/资讯杂志/docs/OPPORTUNITY_SOURCE_DOMAINS.md)
- [docs/OPPORTUNITY_V2_RUNBOOK.md](I:/资讯杂志/docs/OPPORTUNITY_V2_RUNBOOK.md)
- [docs/opportunity_real_benchmark.template.json](I:/资讯杂志/docs/opportunity_real_benchmark.template.json)
- [requirements.txt](I:/资讯杂志/requirements.txt)
- [scripts/test_opportunity_ai_live.py](I:/资讯杂志/scripts/test_opportunity_ai_live.py)
- [services/opportunity/ai_prompt.py](I:/资讯杂志/services/opportunity/ai_prompt.py)
- [services/opportunity/ai_researcher.py](I:/资讯杂志/services/opportunity/ai_researcher.py)
- [services/opportunity/evaluation.py](I:/资讯杂志/services/opportunity/evaluation.py)
- [services/opportunity/facts.py](I:/资讯杂志/services/opportunity/facts.py)
- [services/opportunity/fetcher.py](I:/资讯杂志/services/opportunity/fetcher.py)
- [services/opportunity/models.py](I:/资讯杂志/services/opportunity/models.py)
- [services/opportunity/pdf_extract.py](I:/资讯杂志/services/opportunity/pdf_extract.py)
- [services/opportunity/pipeline.py](I:/资讯杂志/services/opportunity/pipeline.py)
- [services/opportunity/project_memory.py](I:/资讯杂志/services/opportunity/project_memory.py)
- [services/opportunity/providers/base.py](I:/资讯杂志/services/opportunity/providers/base.py)
- [services/opportunity/providers/openai_provider.py](I:/资讯杂志/services/opportunity/providers/openai_provider.py)
- [services/opportunity/publisher.py](I:/资讯杂志/services/opportunity/publisher.py)
- [services/opportunity/rules.py](I:/资讯杂志/services/opportunity/rules.py)
- [services/opportunity/runtime.py](I:/资讯杂志/services/opportunity/runtime.py)
- [services/opportunity/search/__init__.py](I:/资讯杂志/services/opportunity/search/__init__.py)
- [services/opportunity/search/existing_search.py](I:/资讯杂志/services/opportunity/search/existing_search.py)
- [services/opportunity/search/glm_web_search.py](I:/资讯杂志/services/opportunity/search/glm_web_search.py)
- [services/opportunity/search/provider_native_search.py](I:/资讯杂志/services/opportunity/search/provider_native_search.py)
- [services/opportunity/settings.py](I:/资讯杂志/services/opportunity/settings.py)
- [services/opportunity/sources/existing_skills.py](I:/资讯杂志/services/opportunity/sources/existing_skills.py)
- [services/opportunity/sources/search.py](I:/资讯杂志/services/opportunity/sources/search.py)
- [services/opportunity/verifier.py](I:/资讯杂志/services/opportunity/verifier.py)
- [templates/opportunity_fields.html](I:/资讯杂志/templates/opportunity_fields.html)
- [tests/test_ai_fallback.py](I:/资讯杂志/tests/test_ai_fallback.py)
- [tests/test_glm_web_search.py](I:/资讯杂志/tests/test_glm_web_search.py)
- [tests/test_opportunity_review2.py](I:/资讯杂志/tests/test_opportunity_review2.py)
