# 商机来源域名维护

`source_tier`按hostname及子域名边界匹配，默认政府/公共资源域名沿用基线。`trusted_domains`、`authoritative_domains`为已有配置接口，authoritative_domains保持空；trusted_domains仅收录已逐项确认的平台。没有真实项目确认记录的企业域名不加入。

增加记录时保存：域名、层级、业主/采购主体、公告URL、主体确认依据、确认日期、审核人。拒绝整段URL、通配符、包含凭据的配置。域名Tier1不代表每个页面都是一手公告，仍需同项目原文和逐字段Evidence。确认企业域名属于真实灰度数据工作，当前不声称已完成。

## 已确认来源

- 域名：cg.ccteg.cn；层级：Tier 1；主体：中国煤科电子采购平台。
- 依据：2026-10-08直接获取 https://cg.ccteg.cn/cms/channel/ywgg1hw/87960.htm 原文，正文明确以该平台完成招标文件购买、投标递交、开标和CA办理，业主为天地（唐山）矿业科技有限公司。
- 核验：Codex根据用户提供链接直接获取；归档在本地runtime/evaluations/gpt_reference_2026-10-08.json。仅确认平台身份，具体公告仍逐字段核验。
