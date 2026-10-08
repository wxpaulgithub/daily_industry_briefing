# 第二轮真实评测流程

当前只交付机制，不将synthetic测试或空模板当作真实质量达标。人工收集30–50条真实项目（含负例），保存出处与裁决理由；2–4周灰度及至少100条人工裁决后按任务书验收。

## 数据格式

复制opportunity_real_benchmark.template.json，evaluation_date为标签对应日期，cases为数组。每条需要title、url、expected_relevant（boolean）、human_reason。建议project_id、project_key、url_aliases、expected_stage、expected_budget、expected_owner、expected_deadline、expected_top5，以及人工Company Fit/切入建议1–5分。

research模式需要documents数组，至少有原文url、text、title、retrieval_method（html/pdf）；PDF可含pages（page/text），附件含parent_url。--prepare --fetch-sources获取并冻结当前原文；历史评测应使用当时存档，不能把今天已更改的原文冒充历史版本。取不到原文时如实失败，不伪造文件或样本。

## 三类结果

- research：相同冻结来源，每家Provider的候选对象隔离，按evaluation_date运行。候选Recall不能解释为联网发现召回。
- discovery：各自真实联网，日期必须是今天。标注池是有限基准，未标注预测单列供人工裁决，不算误报，也不计正确。
- longitudinal：无API调用，读取跨日快照，material_hash包含公告版本；合法更新不算无变化重复。网页保留旧项目的重复出现率不等于推送重复，另外报告由持久化确认时间推导的投递事件及重复率。

正式核验结果、preview、规则fallback分开统计。事实准确率只评价已抽取字段，完整率分母包括漏报和字段缺失；expected为空表示没有可抽取的已知金标，不进入要求完整的分母。关键事实准确率必须结合完整率看，不能通过少输出宣称质量改善。

未配置API Key/模型、没有人工金标或尚未灰度时，记录未完成，不宣称上线。运行命令见OPPORTUNITY_V2_RUNBOOK.md。收费接口必须--live，任何模式不调用企微发布、不保存正式日报。

## API来源依据

OpenAI实际来源读取web_search_call.action.sources，仅将URL当检索线索，不将模型summary当工具snippet；接口依据：[OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)。
