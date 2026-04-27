# 智能仓储每日简讯

自动采集工业/制造业/智能仓储领域的资讯，以科技杂志风格排版为 Web 页面展示。支持四范围分页、多主题切换和微信公众号兼容输出。

---

## 目录

- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [信息采集架构总览](#信息采集架构总览)
- [各信源详细采集机制](#各信源详细采集机制)
  - [1. 今日头条搜索（国内主资讯）](#1-今日头条搜索国内主资讯)
  - [2. 微信公众号 RSS（公众号主通道）](#2-微信公众号-rss公众号主通道)
  - [3. 微信公众号搜狗搜索（可选兜底）](#3-微信公众号搜狗搜索可选兜底)
  - [4. 知乎发现（发现页信源）](#4-知乎发现发现页信源)
  - [5. B站发现（发现页信源）](#5-b站发现发现页信源)
  - [6. 招投标公告（混合信源）](#6-招投标公告混合信源)
  - [7. 政策标准（RSS 信源）](#7-政策标准rss-信源)
  - [8. 行业媒体（RSS 信源）](#8-行业媒体rss-信源)
  - [9. 展会协会（RSS 信源）](#9-展会协会rss-信源)
  - [10. 本地项目（本地资讯）](#10-本地项目本地资讯)
- [反爬机制与应对策略详解](#反爬机制与应对策略详解)
- [公众号信息收集完整链路](#公众号信息收集完整链路)
- [知乎与B站的反爬实现](#知乎与b站的反爬实现)
- [如何修改关键词改变搜索内容](#如何修改关键词改变搜索内容)
- [如何刷新搜索结果](#如何刷新搜索结果)
  - [全局刷新](#全局刷新)
  - [单 Tab 独立刷新](#单-tab-独立刷新)
  - [页面内刷新按钮](#页面内刷新按钮)
- [图片获取与处理](#图片获取与处理)
  - [图片来源优先级](#图片来源优先级)
  - [无图文章的处理策略](#无图文章的处理策略)
  - [图片防盗链的应对](#图片防盗链的应对)
  - [图片本地化流程](#图片本地化流程)
- [四范围 Tab 页面体系](#四范围-tab-页面体系)
- [内容过滤与精选机制](#内容过滤与精选机制)
- [Cookie 配置指南](#cookie-配置指南)
- [公众号来源配置](#公众号来源配置)
- [页面主题系统](#页面主题系统)
- [云端部署](#云端部署)
- [微信公众号发布](#微信公众号发布)
- [定时任务配置](#定时任务配置)

---

## 项目结构

```
资讯杂志/
├── app.py                  # FastAPI Web 服务入口（路由、定时任务、局部刷新）
├── fetch.py                # 独立采集脚本（不启动服务，适合命令行/计划任务）
├── config.py               # 全局配置（关键词、数量、超时、Cookie 路径等）
├── requirements.txt        # Python 依赖
├── config_data/
│   └── wechat_sources.json # 公众号账号池与搜索词配置（配置驱动）
├── runtime/
│   └── cookie.txt          # 知乎/B站登录态 Cookie（分节格式）
├── services/
│   ├── fetcher.py          # Skill 基类 + 过滤/去重/打分/精选/范围分流
│   ├── generator.py        # HTML 页面生成器（Jinja2）+ 图片本地化
│   ├── cookie_store.py     # Cookie 文件解析与缓存（支持热重载）
│   ├── wechat_sources.py   # 公众号配置读取（热加载、scope 过滤）
│   └── skills/             # 可插拽数据源 Skill 目录
│       ├── __init__.py              # Skill 注册表
│       ├── toutiao.py               # 今日头条搜索
│       ├── wechat.py                # 微信公众号搜狗搜索（可选兜底）
│       ├── wechat_rss.py            # 微信公众号 RSS 主通道
│       ├── zhihu_discover.py        # 知乎发现
│       ├── bilibili_discover.py     # B站发现
│       ├── bidding.py               # 招投标公告
│       ├── policy.py                # 政策标准
│       ├── industry_media.py        # 行业媒体
│       ├── expo_assoc.py            # 展会协会
│       ├── local_projects.py        # 本地项目
│       ├── local_wechat.py          # 本地公众号（搜狗通道）
│       ├── discover_base.py         # 发现类 Skill 通用基类（含 Bing 兜底）
│       └── rss_generic.py           # RSS/HTML 通用基类（含关键词过滤）
├── templates/
│   ├── magazine.html       # Web 版模板（四范围 Tab + 3种主题 + 刷新按钮）
│   └── wechat.html         # 微信公众号版模板（纯内联 CSS）
├── static/                 # 静态资源（Logo、备用图片等）
├── output/                 # 生成的每日 HTML/JSON + images/
└── scripts/
    ├── update_cookie.py               # 交互式 Cookie 更新工具
    ├── read_cookie_file.py            # Cookie 状态检查
    └── refresh_and_upload_cookie.ps1  # Cookie 远程上传脚本
```

---

## 快速开始

```bash
cd "G:\资讯杂志"

# 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 方式一：独立采集（只生成页面不启动服务）
python fetch.py

# 方式二：启动 Web 服务（含自动采集 + 定时任务）
python app.py
```

采集完成后，`output/` 目录生成以下文件：

| 文件 | 说明 |
|------|------|
| `{日期}.html` | Web 版（主题由前端 CSS 切换） |
| `{日期}_wechat.html` | 微信公众号版，可直接复制粘贴到编辑器 |
| `{日期}.json` | 原始数据 JSON，供 API 动态渲染 |
| `images/` | 下载到本地的文章图片（解决防盗链） |

---

## 信息采集架构总览

系统采用**可插拔 Skill 架构**，每个数据源实现为一个独立的 Skill 类，继承 `NewsSkill` 基类。所有 Skill 并发采集后，经过统一的过滤、去重、打分、精选流程，最终按四个范围（国内/本地/公众号/发现）分别输出。

```
                        Skill 并发采集层
                    ┌──────────────────────┐
                    │  _run_skill_with_guard │  (超时保护、缓存、冷却、降级)
                    └──────────┬───────────┘
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                     ▼
   ┌──────────────┐   ┌──────────────┐    ┌───────────────┐
   │  国内/本地    │   │  发现页      │    │  公众号       │
   │  ToutiaoSkill│   │  ZhihuSkill  │    │  WeChatRss    │
   │  PolicySkill │   │  BilibiliSkill│    │  WeChatSkill  │
   │  BiddingSkill│   │              │    │  (搜狗兜底)   │
   │  MediaSkill  │   └──────┬───────┘    └───────┬───────┘
   │  ExpoSkill   │          │                    │
   │  LocalProject│          │                    │
   └──────┬───────┘          │                    │
          └──────────────────┼────────────────────┘
                             ▼
                    filter_relevant()    ← 排除无关内容
                             ▼
                    deduplicate()        ← 标题相似度去重
                             ▼
                    score_article()      ← 打分（时效、图片、来源、厂商）
                             ▼
                    select_articles()    ← 按范围分别精选
                    ┌─────────┬─────────┬──────────┐
                    ▼         ▼         ▼          ▼
                 national   local    discover   wechat
                  (24条)    (12条)    (18条)     (18条)
```

### Skill 运行态保护机制

每个 Skill 都被 `_run_skill_with_guard` 包裹，提供以下保护：

| 保护机制 | 配置参数 | 说明 |
|----------|----------|------|
| 超时保护 | `SKILL_FETCH_TIMEOUT_SECONDS = 45` | 单个 Skill 抓取超过 45 秒自动中断 |
| 结果缓存 | `SKILL_CACHE_TTL_SECONDS = 1800` | 抓取成功后缓存 30 分钟，期间不重复抓取 |
| 失败冷却 | `SKILL_FAILURE_THRESHOLD = 2` | 连续失败 2 次后进入 15 分钟冷却期 |
| 降级兜底 | - | 抓取失败时优先使用缓存结果 |

---

## 各信源详细采集机制

### 1. 今日头条搜索（国内主资讯）

**Skill 文件**：`services/skills/toutiao.py`
**范围**：`national`（国内）
**采集方式**：直接调用今日头条搜索 API，无需 Cookie，无需翻墙

**工作原理**：

```
关键词 "智能制造 工业自动化 智能仓储"
         │
         ▼
GET https://www.toutiao.com/api/search/content/
    ?keyword=...
    &pd=information
    &source=input
    &dvpf=pc
    &aid=4916
    &page_num=0
    &count=10
         │
         ▼
    解析 JSON 响应中 data[] 数组
    提取 title / share_url / abstract / media_name / image_url
```

**特点**：
- 时效性最强，覆盖面广，图片覆盖率高
- 不需要登录态或 Cookie
- 7 组关键词并发搜索，每组返回最多 10 条

### 2. 微信公众号 RSS（公众号主通道）

**Skill 文件**：`services/skills/wechat_rss.py`
**范围**：`wechat`（行业公众号）+ `local`（本地公众号）
**采集方式**：消费上游 we-mp-rss 服务生成的 RSS Feed

**工作原理**：

```
config_data/wechat_sources.json
    │
    ├── sources[].rss_url → RSS Feed URL
    │
    ▼
GET {rss_url}
    Accept: application/rss+xml
         │
         ▼
    feedparser 解析 RSS/Atom
    提取 title / link / summary / published / media_content
         │
         ▼
    无封面图的文章 → 并发访问微信文章页面
    提取 og:image 或 msg_cdn_url 作为封面
```

**封面图提取策略**（`_fetch_cover_image`）：

微信 RSS 本身不提供图片数据，系统会额外访问文章页面提取封面：

1. 请求 `mp.weixin.qq.com/s?...` 页面（只读取前 50KB）
2. 用正则匹配 `<meta property="og:image" content="...">`
3. 若未命中，尝试匹配 `var msg_cdn_url = "..."`
4. 并发限制：信号量 `Semaphore(5)`，避免触发微信风控

**配置驱动**：

账号和搜索词通过 `config_data/wechat_sources.json` 管理，支持：
- `scopes: ["wechat"]` -- 进入"公众号"页
- `scopes: ["local"]` -- 进入"本地"页
- `scopes: ["wechat", "local"]` -- 同时出现在两个页面
- `enabled: false` -- 临时禁用某账号

### 3. 微信公众号搜狗搜索（可选兜底）

**Skill 文件**：`services/skills/wechat.py`
**范围**：`wechat`
**采集方式**：通过搜狗微信搜索（weixin.sogou.com）按关键词搜索公众号文章
**默认状态**：关闭（设置 `USE_SOGOU_WECHAT_FALLBACK=true` 启用）

**工作原理**：

```
关键词 "智能仓储 立体仓库 堆垛机"
         │
         ▼
GET https://weixin.sogou.com/weixin
    ?type=2          ← type=2 搜索文章（非公众号）
    &query=...
    &ie=utf8
         │
         ▼
    解析 HTML 搜索结果页
    ┌─────────────────────────────────────┐
    │ <ul class="news-list">             │
    │   <li id="sogou_vr_...">          │
    │     <h3><a href="/link?url=...">  │ ← 搜狗跳转链接
    │     <p class="txt-info">摘要</p>  │
    │     <span class="all-time-y2">    │ ← 公众号名称
    │     timeConvert('unix_ts')        │ ← 发布时间
    │     <img src="...">               │ ← 缩略图
    └─────────────────────────────────────┘
         │
         ▼
    解析搜狗跳转链接 → 获取真实微信文章 URL
    （详见下方"反爬机制"章节）
```

**反爬策略**：搜狗有严格的频率限制。本 Skill 采用：
- **顺序请求**（不并发）：每组关键词依次搜索
- **随机延时**：每组间隔 `random.uniform(4.5, 7.0)` 秒
- **全局冷却**：命中反爬后所有关键词停止搜索，进入 300 秒冷却
- **反爬页面检测**：检查响应中是否包含 `antispider`、`verify`、`captcha` 等关键词

### 4. 知乎发现（发现页信源）

**Skill 文件**：`services/skills/zhihu_discover.py`
**范围**：`discover`（发现页）
**采集方式**：知乎搜索 API（需 Cookie）+ HTML 状态数据解析 + Bing 兜底

**三层降级采集策略**：

```
第一层：知乎搜索 API（需登录态 Cookie）
    │
    GET https://www.zhihu.com/api/v4/search_v3
        ?t=general&q=...&limit=10
        Cookie: {zhihu_cookie}
        Referer: https://www.zhihu.com/
    │
    ├─ 成功(200) → 解析 data[].object 提取标题/URL/摘要/缩略图
    │              URL 强制网页化：api/v4/questions/{id} → zhihu.com/question/{id}
    │
    └─ 失败 → 第二层
              │
              第二层：HTML 页面状态数据解析
              │
              GET https://www.zhihu.com/search?type=content&q=...
              │
              ├─ 解析 <script id="js-initialData"> 内嵌 JSON
              ├─ 解析 <script id="__NEXT_DATA__"> 内嵌 JSON
              ├─ 深度遍历 JSON 树，提取含 title/url 的节点
              ├─ 兜底：正则提取 question/zhuanlan 链接
              │
              └─ 失败 → 第三层
                        │
                        第三层：Bing 站内搜索兜底
                        │
                        GET https://www.bing.com/search
                            ?q=site:zhihu.com/question ... {keyword}
                        │
                        ├─ 解析 Bing 结果页中的知乎链接
                        ├─ 解码 Bing 跳转链接（base64 解码）
                        └─ 兜底：返回知乎搜索入口链接
```

**Cookie 状态检测**：

系统每 10 分钟检测一次知乎 Cookie 是否有效：

```
GET https://www.zhihu.com/api/v4/me
    Cookie: {zhihu_cookie}
    │
    ├─ 200 → Cookie 有效
    └─ 非200 → Cookie 失效，匿名模式继续（降级到 HTML/Bing）
```

### 5. B站发现（发现页信源）

**Skill 文件**：`services/skills/bilibili_discover.py`
**范围**：`discover`（发现页）
**采集方式**：B站搜索 API（可选 Cookie）+ HTML 兜底 + Bing 兜底

**三层降级采集策略**：

```
第一层：B站搜索 API
    │
    GET https://api.bilibili.com/x/web-interface/search/type
        ?search_type=video&keyword=...&order=pubdate
        Cookie: {bilibili_cookie}（可选）
        Referer: https://search.bilibili.com/
        Origin: https://search.bilibili.com
    │
    ├─ 成功 → 解析 data.result[] 提取 title/bvid/arcurl/description/pic/pubdate
    │         清理标题中的高亮标签 <keyword>...</keyword>
    │
    └─ 失败 → 第二层
              │
              第二层：HTML 页面链接提取
              │
              GET https://search.bilibili.com/all?keyword=...
              │
              └─ 正则提取 bilibili.com/video/BV... 链接
                 │
                 └─ 失败 → 第三层
                           │
                           第三层：Bing 站内搜索兜底
                           │
                           GET https://www.bing.com/search
                               ?q=site:bilibili.com/video {keyword}
```

### 6. 招投标公告（混合信源）

**Skill 文件**：`services/skills/bidding.py`
**范围**：`national`（国内）
**采集方式**：公告站点 RSS/HTML + 今日头条搜索补充

**双层采集**：

```
第一层：政府采购公告站点（RSS/HTML 解析）
    ├── 中国政府采购网采购信息
    ├── 中国政府采购网地方采购
    ├── 全国公共资源交易平台
    └── 中国招标投标公共服务平台
    │
    │  使用 RSSKeywordSkill 基类：
    │  1. 优先按 RSS/Atom 解析
    │  2. RSS 失败则按 HTML 列表页解析（提取 <a href> 标签）
    │  3. 按 include_keywords / exclude_keywords 过滤
    │
    ▼
第二层：今日头条关键词搜索补充
    │
    │  5 组招投标相关关键词
    │  搜索结果同样按 include/exclude 过滤
    │
    ▼
合并 + URL 去重
```

### 7. 政策标准（RSS 信源）

**Skill 文件**：`services/skills/policy.py`
**范围**：`national`

订阅的 RSS 源：

| 来源 | URL |
|------|-----|
| 中国政府网政务动态 | `https://www.gov.cn/rss/yaowen.xml` |
| 工信部要闻 | `https://www.miit.gov.cn/xwdt/gxdt/index.html` |
| 发改委要闻 | `https://www.ndrc.gov.cn/xwdt/xwfb/rss.xml` |

按 `include_keywords`（智能制造、工业互联网、数字化转型等）和 `exclude_keywords`（文旅、体育、教育考试等）过滤。

### 8. 行业媒体（RSS 信源）

**Skill 文件**：`services/skills/industry_media.py`
**范围**：`national`

订阅的 RSS 源：

| 来源 | URL |
|------|-----|
| 36氪资讯 | `https://36kr.com/feed` |
| 虎嗅24小时 | `https://www.huxiu.com/rss/0.xml` |
| 创业邦 | `https://www.cyzone.cn/rss.xml` |

### 9. 展会协会（RSS 信源）

**Skill 文件**：`services/skills/expo_assoc.py`
**范围**：`national`

订阅的 RSS 源：

| 来源 | URL |
|------|-----|
| 中国物流与采购联合会 | `http://www.chinawuliu.com.cn/rss.xml` |
| 中国机械工业联合会 | `http://www.cmif.org.cn/rss.xml` |

### 10. 本地项目（本地资讯）

**Skill 文件**：`services/skills/local_projects.py`
**范围**：`local`（本地）
**采集方式**：今日头条搜索 API

与 ToutiaoSkill 使用相同的搜索 API，但关键词自动拼接本地区域词（"无锡"、"新吴区"、"锡山区"等），定向搜索本地项目、招商、建设相关资讯。

---

## 反爬机制与应对策略详解

### 搜狗微信搜索的反爬与应对

搜狗微信搜索（weixin.sogou.com）是最具挑战性的信源，反爬策略包括：

**搜狗的反爬手段**：
1. **频率限制**：短时间内多次请求会触发验证码/antispider 页面
2. **跳转链接**：搜索结果中的链接不是真实微信文章 URL，而是搜狗跳转链接（`sogou.com/link?url=...`），需要多次跳转才能拿到真实地址
3. **JS 混淆**：跳转页面使用 JS 变量拼接、unescape 编码等手段隐藏真实 URL

**本系统的应对策略**：

| 策略 | 实现 | 代码位置 |
|------|------|----------|
| 顺序请求 | 不并发，逐个关键词搜索 | `wechat.py:721-739` |
| 随机延时 | 每组间隔 4.5-7 秒 | `wechat.py:736` |
| 反爬检测 | 检查 antispider/verify/captcha 关键词 | `wechat.py:75-80` |
| 全局冷却 | 命中反爬后停止所有搜索，300 秒冷却 | `wechat.py:46-47` |
| 多阶段 URL 解析 | 5 层递进式解析真实链接 | `wechat.py:529-694` |
| 串行+抖动解析 | 跳转链接逐个解析，间隔 0.2-0.6 秒 | `wechat.py:697-699` |
| 公众号历史回溯 | 跳转失败后搜索公众号主页，从历史发布中匹配 | `wechat.py:536-587` |
| 搜索页兜底 | 全部失败则降级为搜狗搜索结果页链接 | `wechat.py:703-706` |

**搜狗跳转链接解析的 5 层策略**（按优先级）：

```
第1层：从搜狗 URL 参数直接提取
    sogou.com/link?url=... → 解码 url 参数

第2层：不跟随重定向，读取 Location 头
    GET sogou.com/link?url=... (follow_redirects=False)
    → 读取响应头 Location

第3层：解析跳转页面 HTML/JS
    ├─ url += '...' 拼接模式
    ├─ window.location.replace('...') 直接跳转
    ├─ var j='...'; location.href=j; 变量赋值模式
    ├─ unescape('...') 编码模式
    └─ mp.weixin.qq.com/s?... 直接匹配

第4层：跟随重定向获取最终 URL
    GET sogou.com/link?url=... (follow_redirects=True)
    → 从最终页面提取 og:url / msg_link / mp 链接

第5层：公众号历史发布回溯
    ├─ 搜索公众号主页（sogou type=1）
    ├─ 解析 msgList JSON 提取历史文章列表
    └─ 按标题相似度 + 时间匹配最佳文章
```

### 知乎的反爬与应对

**知乎的反爬手段**：
1. **登录态限制**：匿名访问搜索 API 返回 403
2. **验证码**：频繁请求触发人机验证
3. **Cookie 过期**：`z_c0` 凭证几天到几周失效

**本系统的应对策略**：

| 策略 | 说明 |
|------|------|
| Cookie 登录态 | 从 `runtime/cookie.txt` 读取知乎 Cookie，缓存 10 分钟有效性检测 |
| HTML 状态数据解析 | 从页面 `<script id="js-initialData">` 和 `<script id="__NEXT_DATA__">` 提取内嵌 JSON |
| Bing 站内搜索 | 最终兜底，通过 Bing 搜索 `site:zhihu.com` 域名的结果 |
| API URL 网页化 | 将 API 链接（`api/v4/questions/123`）强制转为可访问的网页链接（`zhihu.com/question/123`） |
| 保底入口 | 所有方法都失败时返回知乎搜索页面链接，确保发现页不为空 |

### B站的反爬与应对

**B站的限制**：
1. 搜索 API 对匿名请求有一定容忍度，但部分场景会限制
2. 搜索结果中的关键词高亮标签需要清理

**本系统的应对策略**：

| 策略 | 说明 |
|------|------|
| 可选 Cookie | 支持从 `runtime/cookie.txt` 读取 B站 Cookie 提高成功率 |
| HTML 兜底 | API 失败后直接抓取 B站搜索页，正则提取视频链接 |
| Bing 兜底 | 最终兜底，搜索 `site:bilibili.com/video` |
| 标签清理 | 清理 `<keyword>` 等 B站搜索高亮标签 |

### RSS 源的容错机制

**RSSKeywordSkill 基类**（`rss_generic.py`）提供：

| 策略 | 说明 |
|------|------|
| 指数退避重试 | 最多 3 次尝试，间隔 2/4/8 秒 |
| RSS 优先 | 优先用 feedparser 解析 RSS/Atom |
| HTML 兜底 | RSS 为空则按 HTML 列表页解析（提取 `<a href>` 标签） |
| 关键词过滤 | include_keywords 白名单 + exclude_keywords 黑名单 |

---

## 公众号信息收集完整链路

本系统收集公众号信息有**两条链路**，RSS 为主，搜狗为兜底：

```
                    公众号信息收集
                         │
          ┌──────────────┴──────────────┐
          ▼                              ▼
    主通道：RSS                    兜底：搜狗搜索
    (WeChatRssSkill)              (WeChatSkill, 默认关闭)
          │                              │
          ▼                              ▼
    上游 we-mp-rss 服务            搜狗微信搜索
    把公众号转为 RSS               按关键词搜索文章
          │                              │
          ▼                              ▼
    config_data/                   sogou.com/weixin
    wechat_sources.json            ?type=2&query=...
    配置 rss_url                          │
          │                              ▼
          ▼                         解析 HTML 结果
    feedparser 解析                提取跳转链接
    提取标题/链接/摘要                    │
          │                              ▼
          ▼                         5层策略解析
    无封面图 →                      真实微信文章URL
    访问文章页面                    (详见反爬章节)
    提取 og:image                        │
          │                              ▼
          ▼                         公众号历史回溯
    按 scopes 分流:                (标题匹配)
    wechat → 公众号页                   │
    local  → 本地页                     ▼
                                  按账号配置过滤
                                  (wechat_sources.json)
```

**配置驱动设计**：

所有公众号账号通过 `config_data/wechat_sources.json` 统一管理：

```json
{
  "sources": [
    {
      "name": "无锡发布",
      "enabled": true,
      "scopes": ["local"],
      "aliases": ["无锡市人民政府发布"],
      "rss_url": "http://.../feed/MP_WXS_2395797954.rss"
    },
    {
      "name": "中国物流与采购",
      "enabled": true,
      "scopes": ["wechat"],
      "aliases": ["中物联"],
      "rss_url": "http://.../feed/MP_WXS_3093520807.rss"
    }
  ],
  "queries": {
    "wechat": [
      {"keyword": "立体库 堆垛机 输送线 提升机", "label": "立体库设备"},
      {"keyword": "AGV AMR 调度 仓储机器人", "label": "移动机器人"}
    ],
    "local": [
      {"keyword": "无锡 新吴区 项目 开工 投产", "label": "本地项目"}
    ]
  }
}
```

配置文件修改后无需重启服务，系统按文件修改时间自动热加载。

---

## 知乎与B站的反爬实现

### 知乎的三层采集详解

**第一层：搜索 API（需 Cookie）**

```python
# 请求知乎搜索 API
GET https://www.zhihu.com/api/v4/search_v3
    ?t=general
    &q=智能仓储 立体库 堆垛机
    &correction=1
    &offset=0
    &limit=10
Headers:
    Cookie: {从 runtime/cookie.txt 读取}
    Referer: https://www.zhihu.com/
    Accept: application/json
```

API 返回结构化数据，从 `data[].object` 中提取：
- `title` -- 文章标题
- `url` -- 原始 URL（可能是 API 格式，需要转换）
- `excerpt` -- 摘要
- `thumbnail` / `image_url` / `cover_url` -- 图片
- `author.name` -- 作者名

**URL 强制网页化**（关键步骤）：

知乎 API 返回的 URL 通常是 API 格式，不能直接在浏览器打开。系统会强制转换：

| API URL | 网页 URL |
|---------|----------|
| `/api/v4/questions/123` | `https://www.zhihu.com/question/123` |
| `/api/v4/articles/456` | `https://zhuanlan.zhihu.com/p/456` |
| `/api/v4/answers/789` | `https://www.zhihu.com/question/{qid}/answer/789` |

**第二层：HTML 状态数据解析**

```python
# 请求知乎搜索页面
GET https://www.zhihu.com/search?type=content&q=...

# 解析内嵌的状态数据
# 方式1：<script id="js-initialData">{JSON}</script>
# 方式2：<script id="__NEXT_DATA__">{JSON}</script>

# 深度遍历 JSON 树，找到所有包含 title + url 的节点
for node in _walk_dicts(data):
    if node.get("title") and (node.get("url") or node.get("id")):
        → 构造 Article
```

**第三层：Bing 站内搜索兜底**

```python
# 通过 Bing 搜索知乎内容
GET https://www.bing.com/search
    ?q=site:zhihu.com/question OR site:zhuanlan.zhihu.com/p 智能仓储

# 解码 Bing 跳转链接
# Bing 常见格式: bing.com/ck/a?...&u=a1aHR0cHM6Ly93d3cuemhpaHUuY29tL...
#                                            └── base64 编码的真实 URL
# 解码: 去掉前缀 "a1"，base64 解码得到真实 URL
```

### B站的三层采集详解

**第一层：B站搜索 API**

```python
GET https://api.bilibili.com/x/web-interface/search/type
    ?search_type=video
    &keyword=WMS WCS AGV
    &page=1
    &order=pubdate
Headers:
    Cookie: {可选}
    Referer: https://search.bilibili.com/
    Origin: https://search.bilibili.com
```

B站搜索结果中标题包含 `<keyword>` 高亮标签，系统会清理：
```python
# 清理前: <keyword class="keyword">AGV</keyword>仓储
# 清理后: AGV仓储
```

**第二层：HTML 链接提取**

```python
GET https://search.bilibili.com/all?keyword=...

# 正则提取视频链接
re.finditer(r'https://www\.bilibili\.com/video/BV[0-9A-Za-z]+', html)
```

**第三层：Bing 兜底**（同知乎）

### 发现页双源均衡策略

发现页从知乎和B站两个来源采集，采用**双源均衡选取**确保不会一方压倒另一方：

```python
def _select_discover_articles(articles, count):
    # 1. 知乎/B站各占一半配额
    zhihu_quota = count // 2
    bilibili_quota = count - half

    # 2. 各自按质量分独立选取
    zhihu_selected = pick(zhihu_pool, zhihu_quota)
    bilibili_selected = pick(bilibili_pool, bilibili_quota)

    # 3. 未用完的配额开放给有余量的一方
    # 4. 仍不足则用 Bing 占位兜底
    # 5. 最终按质量分排序展示
```

---

## 如何修改关键词改变搜索内容

资讯内容由三层控制：**Skill 搜索关键词** → **内容过滤规则** → **精选参数**。

### 第一层：修改搜索关键词（决定拉回什么内容）

直接编辑对应 Skill 文件的 `search_queries` 属性：

**今日头条**（`services/skills/toutiao.py`）：

```python
@property
def search_queries(self) -> list[dict]:
    return [
        {"keyword": "智能制造 工业自动化 智能仓储 立库 堆垛机", "label": "智能制造"},
        {"keyword": "智能工厂 集成 数字化工厂 AGV", "label": "智能工厂"},
        # ↑ 修改这里的关键词即可改变搜索内容
        # 空格分隔表示 OR 关系，结果会包含任意一个关键词
    ]
```

**公众号 RSS**（`config_data/wechat_sources.json`）：

```json
{
  "queries": {
    "wechat": [
      {"keyword": "立体库 堆垛机 输送线 提升机", "label": "立体库设备"},
      {"keyword": "AGV AMR 调度 仓储机器人", "label": "移动机器人"}
    ]
  }
}
```

**知乎发现**（`services/skills/zhihu_discover.py`）：

```python
@property
def search_queries(self) -> list[dict]:
    return [
        {"keyword": "智能仓储 立体库 堆垛机", "label": "仓储"},
        {"keyword": "WMS WCS AGV AMR", "label": "系统"},
        {"keyword": "物流自动化 工厂改造", "label": "改造"},
        {"keyword": "仓储项目 招标 中标", "label": "项目"},
    ]
```

**B站发现**（`services/skills/bilibili_discover.py`）：

```python
@property
def search_queries(self) -> list[dict]:
    return [
        {"keyword": "智能仓储 立体库", "label": "仓储"},
        {"keyword": "WMS WCS AGV", "label": "系统"},
        {"keyword": "物流自动化 项目案例", "label": "案例"},
        {"keyword": "工厂自动化 改造", "label": "改造"},
    ]
```

**招投标**（`services/skills/bidding.py`）：

```python
search_queries_toutiao = [
    "智能仓储 招标 中标 公告",
    "立体库 堆垛机 输送线 招标 采购",
    "WMS WCS AGV 招标 中标",
    # ↑ 修改这里控制招投标搜索方向
]
```

**本地项目**（`services/skills/local_projects.py`）：

```python
# 区域关键词在 config.py 中配置
LOCAL_REGION_KEYWORDS = [
    "无锡", "无锡市", "新区", "新吴区", "锡山区", "惠山区", "滨湖区",
]
# 区域词会自动拼接到搜索关键词前面
```

### 第二层：修改过滤规则（决定保留什么内容）

编辑 `services/fetcher.py`：

**必选关键词**（`REQUIRED_KEYWORDS`）-- 标题或摘要必须命中至少一个：

```python
REQUIRED_KEYWORDS = [
    "制造", "工厂", "产线", "自动化", "智能", "数字化",
    "仓储", "仓库", "立库", "堆垛机", "AGV", "物流",
    "工业", "PLC", "MES", "WMS", "SCADA", "ERP",
    "机器人", "机械臂", "装备", "设备",
    "项目", "中标", "招标", "签约", "投产",
    "展会", "博览会", "论坛", "峰会",
]
```

**排除关键词**（`EXCLUDE_KEYWORDS`）-- 命中任何一个则直接丢弃：

```python
EXCLUDE_KEYWORDS = [
    "村支书", "纪委", "反腐",  # 时政
    "房价", "楼市", "股票",     # 财经
    "明星", "综艺", "娱乐",     # 娱乐
    "人形机器人", "机器狗",     # 非仓储机器人
]
```

### 第三层：修改精选参数

编辑 `config.py`：

```python
MAX_ARTICLES = 24             # 国内页最多展示条数
MAX_ARTICLES_LOCAL = 12       # 本地页最多展示条数
MAX_ARTICLES_DISCOVER = 18    # 发现页最多展示条数
MAX_ARTICLES_WECHAT = 18      # 公众号页最多展示条数
MAX_ARTICLE_AGE_DAYS = 15     # 只选取 15 天内的文章
SUMMARY_MAX_LENGTH = 200      # 摘要最大字符数
```

### 关键词修改速查表

| 想做什么 | 改哪个文件 | 改什么 |
|----------|-----------|--------|
| 改变头条搜索方向 | `skills/toutiao.py` | `search_queries` |
| 改变公众号搜索词 | `config_data/wechat_sources.json` | `queries.wechat` |
| 改变本地搜索词 | `config_data/wechat_sources.json` | `queries.local` |
| 改变知乎搜索词 | `skills/zhihu_discover.py` | `search_queries` |
| 改变B站搜索词 | `skills/bilibili_discover.py` | `search_queries` |
| 改变招投标搜索词 | `skills/bidding.py` | `search_queries_toutiao` |
| 改变本地区域 | `config.py` | `LOCAL_REGION_KEYWORDS` |
| 调整内容相关性门槛 | `fetcher.py` | `REQUIRED_KEYWORDS` |
| 排除某些无关内容 | `fetcher.py` | `EXCLUDE_KEYWORDS` |
| 调整各页条数上限 | `config.py` | `MAX_ARTICLES_*` |

---

## 如何刷新搜索结果

系统支持三种刷新方式：全局刷新、单 Tab 局部刷新、页面内刷新按钮。

### 全局刷新

重新采集所有数据源，生成全新内容：

```bash
# API 方式
curl -X POST http://localhost:8088/api/refresh

# 命令行方式
python fetch.py
```

### 单 Tab 独立刷新

每个 Tab 页面对应一组特定的 Skill，支持**独立刷新**而不影响其他 Tab：

```
/api/refresh?scope=national   → 只刷新国内页（ToutiaoSkill, PolicySkill, BiddingSkill, IndustryMediaSkill, ExpoAssocSkill）
/api/refresh?scope=local      → 只刷新本地页（LocalProjectSkill, WeChatRssSkill local 部分）
/api/refresh?scope=wechat     → 只刷新公众号页（WeChatRssSkill wechat 部分, 可选 WeChatSkill）
/api/refresh?scope=discover   → 只刷新发现页（ZhihuDiscoverSkill, BilibiliDiscoverSkill）
```

**局部刷新的工作原理**：

```
1. 只运行该 scope 对应的 Skill 集合
2. 抓取结果与当天已有数据合并：
   - 替换该 scope 的旧数据
   - 保留其他 scope 的数据不变
3. 重新渲染页面并保存
```

示例：刷新公众号页

```bash
curl -X POST "http://localhost:8088/api/refresh?scope=wechat"
```

系统会：
1. 只运行 `WeChatRssSkill`（和可选的 `WeChatSkill`）
2. 读取当天已有的 `2026-04-26.json`
3. 用新抓取的公众号文章替换旧的公众号文章
4. 保留国内/本地/发现页的数据不变
5. 合并后重新渲染并保存

### 页面内刷新按钮

每个页面底部都有"刷新资讯"按钮，点击后的流程：

```
1. 记录当前页面的 fetched_at 基线时间戳
2. POST /api/refresh?scope={当前scope}
3. 每 2 秒轮询 /api/status 和 /api/news/today
4. 检测到新数据（fetched_at > 基线）且采集完成 → 自动刷新页面
5. 最长等待 3 分钟，超时提示"已超时，稍后重试"
```

---

## 图片获取与处理

### 图片来源优先级

每篇文章的图片按以下优先级获取：

**头条/搜索类信源**：
1. `large_image_url`（高清大图）
2. `image_url`（普通图片）

**RSS/Atom 信源**：
1. `media_content[0].url`（媒体附件）
2. `links` 中 `rel=enclosure` 且 `type=image/*` 的链接
3. `<summary>` HTML 中第一个 `<img src="...">`

**微信公众号 RSS**：
1. RSS 中的 `media_content` 或 `<img>` 标签
2. 若无图，访问微信文章页面提取 `og:image`
3. 若仍无图，尝试 `var msg_cdn_url = "..."`

**知乎**：
1. `thumbnail`
2. `image_url`
3. `cover_url`
4. 作者头像 `author.avatar_url`（最后兜底）

**B站**：
1. `pic` 字段（视频封面）

### 无图文章的处理策略

当文章没有任何图片时，系统采用以下策略：

1. **备用图片池**：使用预置的静态图片轮流填充

```html
{% set fallback_images = ['/static/img.jpg', '/static/og.jpg', '/static/1.JPG', '/static/10.JPG'] %}
{% set _fi = (article.title | length * 3 + article.url | length) % 4 %}
<img src="{{ fallback_images[_fi] }}">
```

根据文章标题长度和 URL 长度的哈希值从 4 张备用图中选取一张，确保同一篇文章总是显示同一张图。

2. **onerror 兜底**：即使有图片 URL 但加载失败，也会自动回退到备用图

```html
<img src="{{ article.image_url }}"
     onerror="this.onerror=null;this.src='{{ fallback_images[_ai] }}';">
```

3. **微信防盗链图过滤**：微信 CDN 返回的小于 5KB 的图片通常是防盗链占位图，系统会主动清除这类假图片，避免展示空白：

```python
# generator.py:120-127
if is_wechat_img and len(content) < 5000 and not is_svg:
    article.image_url = ""  # 清除假图片，让前端使用备用图
```

### 图片防盗链的应对

微信 CDN 和搜狗 CDN 都做了 Referer 校验，直接在页面中引用会 403。系统通过**图片本地化**解决：

```python
# generator.py - download_article_images()

# 根据图片 URL 域名自动设置正确的 Referer
def _get_referer_for_url(url):
    if "sogoucdn.com" in url:
        return "https://weixin.sogou.com/"
    if "mmbiz.qpic.cn" in url or "mmbiz.qlogo.cn" in url:
        return "https://mp.weixin.qq.com/"
    if "zhimg.com" in url:
        return "https://www.zhihu.com/"
    if "hdslb.com" in url or "bilibili.com" in url:
        return "https://www.bilibili.com/"
```

### 图片本地化流程

所有文章图片在页面生成前会自动下载到本地：

```
原始图片 URL
    │
    ▼
GET {image_url}
    Referer: {对应域名}
    User-Agent: Chrome/131...
    │
    ▼
保存到 output/images/{md5(url)[:10]}.jpg
    │
    ▼
替换 article.image_url 为 /images/{filename}
```

**图片处理细节**：
- 下载失败的图片回退到原始 URL（不丢弃）
- 微信防盗链占位图（< 5KB）主动清除
- 文件名使用 URL 的 MD5 前 10 位，避免重复下载
- FastAPI 挂载 `/images` 路径指向 `output/images/` 目录

---

## 四范围 Tab 页面体系

页面通过 URL 参数 `?scope=` 切换四个范围：

| Tab | scope 值 | 对应 Skill | 展示上限 |
|-----|----------|-----------|----------|
| 国内 | `national` | 今日头条、政策标准、招投标、行业媒体、展会协会 | 24 条 |
| 本地 | `local` | 本地项目、本地公众号 RSS、本地白名单公众号 | 12 条 |
| 公众号 | `wechat` | 公众号 RSS（wechat 范围）、搜狗搜索（可选） | 18 条 |
| 发现 | `discover` | 知乎发现、B站发现 | 18 条 |

**访问方式**：

```
http://localhost:8088/                       → 国内（默认）
http://localhost:8088/?scope=local           → 本地
http://localhost:8088/?scope=wechat          → 公众号
http://localhost:8088/?scope=discover        → 发现
```

**页面顶部 Tab 切换**：

页面内置了 `<nav class="scope-switcher">` 导航栏，点击即跳转，当前 Tab 高亮显示。

**数据分流逻辑**（`fetcher.py:846-876`）：

```
所有文章（已过滤去重）
    │
    ├─ region_scope=national → 国内池
    ├─ region_scope=local    → 本地池
    │   + wechat_category=local 的公众号
    │   + 本地白名单公众号
    ├─ region_scope=discover → 发现池
    │   知乎/B站双源均衡选取
    └─ region_scope=wechat   → 公众号池
        排除 local 子类和白名单公众号
```

---

## 内容过滤与精选机制

### 打分规则

每篇文章按以下维度打分（`score_article`）：

| 维度 | 分值 | 条件 |
|------|------|------|
| 基础分 | 0.4 | 所有文章 |
| 时效性 | +0.25 | 24 小时内发布 |
| 时效性 | +0.18 | 72 小时内发布 |
| 时效性 | +0.12 | 7 天内发布 |
| 时效性 | +0.06 | 30 天内发布 |
| 有配图 | +0.12 | image_url 不为空 |
| 摘要长度 | +0.08 | 摘要超过 80 字 |
| 摘要长度 | +0.04 | 摘要超过 30 字 |
| 标题长度 | +0.05 | 标题 10-60 字 |
| 权威来源 | +0.10 | 新华社/人民日报/央视等 |
| 厂商相关 | +0.20 | 命中厂商关键词 |
| 招投标 | +0.18 | 招投标来源或命中关键词 |
| 需求信号 | +0~0.24 | 仓储需求信号分 |

### 精选策略

`select_articles` 采用**蛇形选秀**策略确保来源多样性：

```
skill_A: [a1(0.9), a2(0.7), a3(0.5)]
skill_B: [b1(0.85), b2(0.6)]
skill_C: [c1(0.8), c2(0.65)]

第1轮: a1, b1, c1
第2轮: a2, b2, c2
第3轮: a3
```

每组内部优先选择厂商相关内容，确保重要信息不被埋没。

### 国内页招投标保底

国内页保证至少有 `NATIONAL_BIDDING_MIN_COUNT = 4` 条招投标内容（候选充足时）。如果正常精选的招投标条数不足，会用低分非招投标文章替换来补充。

---

## Cookie 配置指南

### Cookie 文件格式

文件路径：`runtime/cookie.txt`（支持环境变量 `COOKIE_FILE_PATH` 覆盖）

```txt
# 站点 Cookie 配置（运行时读取，修改后无需重启）

[zhihu]
z_c0=...; d_c0=...; _xsrf=...; ...

[bilibili]
SESSDATA=...; bili_jct=...; ...
```

- 分节格式，`[站点名]` 作为分隔
- 修改后无需重启服务，系统按文件修改时间自动重载
- 建议整串 Cookie 全量放入，成功率最高

### 交互式更新 Cookie

```bash
# 仅更新知乎
python scripts\update_cookie.py --site zhihu

# 同时更新知乎+B站
python scripts\update_cookie.py --site both
```

脚本会引导你从浏览器 F12 中复制 Cookie，自动检查关键凭证（知乎检查 `z_c0`，B站检查 `SESSDATA`）。

### 检查 Cookie 状态

```bash
python scripts\read_cookie_file.py
```

### 常见问题

**Q: Cookie 多久过期？**
知乎通常几天到几周。失效后日志出现 `知乎登录态不可用，状态码 401`，重新导出即可。

**Q: B站 Cookie 必须配置吗？**
不是必须的。B站搜索 API 对匿名请求有一定容忍度，配置 Cookie 只是提高成功率。

**Q: 不配置 Cookie 知乎还能用吗？**
可以。系统会自动降级到 HTML 状态数据解析和 Bing 兜底，只是内容质量可能不如登录态。

---

## 公众号来源配置

详见 [config_data/wechat_sources.json](config_data/wechat_sources.json)。

关键配置项：

| 字段 | 说明 |
|------|------|
| `name` | 公众号显示名 |
| `enabled` | 是否启用（`false` 可临时禁用） |
| `scopes` | 投放范围：`wechat`（公众号页）/ `local`（本地页），可同时配置 |
| `aliases` | 名称别名，用于来源匹配（模糊匹配） |
| `rss_url` | we-mp-rss 生成的 RSS 地址 |

`queries` 字段可覆盖各范围的搜索词（仅搜狗兜底通道使用）。

---

## 页面主题系统

Web 版支持 3 种视觉主题，通过 CSS 变量实现即时切换，用户选择保存在 `localStorage`：

| 主题 | 名称 | 风格 | 配色 |
|------|------|------|------|
| `notion` | 纸 | 典雅复古 | 暖白底 `#fdf7ea`，爱马仕橘红 `#E04825` |
| `apple` | 简 | 纯粹极简 | 纯白底 `#FFFFFF`，科技蓝 `#0066CC` |
| `linear` | 深 | 沉浸先锋 | 纯黑底 `#000000`，霓虹电紫 `#6C5CE7` |

---

## 云端部署

### Docker 部署（推荐）

```bash
docker-compose up -d
```

`docker-compose.yml` 配置要点：
- Cookie 文件挂载到容器外（`../news_runtime:/runtime`）
- 输出文件挂载到宿主机（`./output:/app/output`）
- 通过 `SITE_URL` 环境变量设置公开 URL
- 内置健康检查

### 手动部署

```bash
# 安装依赖
pip install -r requirements.txt

# 测试采集
python fetch.py

# 启动服务
python app.py
```

---

## 微信公众号发布

1. 启动服务后访问 `http://localhost:8088/wechat`
2. 全选页面内容（Ctrl+A），复制
3. 登录微信公众号后台，新建图文，粘贴

微信公众号版模板使用纯内联 CSS，兼容公众号编辑器。

---

## 定时任务配置

### 方式一：服务内置定时任务

使用 `python app.py` 启动时，APScheduler 每天自动采集。配置在 `config.py`：

```python
SCHEDULE_HOUR = 7    # 每天 7 点
SCHEDULE_MINUTE = 0
```

### 方式二：Windows 任务计划程序

1. 按 `Win+R`，输入 `taskschd.msc`
2. 创建基本任务，名称：`智能仓储每日简讯采集`
3. 触发器：每天 07:00
4. 操作：启动程序
   - 程序：`G:\资讯杂志\venv\Scripts\python.exe`
   - 参数：`fetch.py`
   - 起始于：`G:\资讯杂志`

### 方式三：独立脚本

```bash
python fetch.py
```

---

## 可用 API 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 今日资讯页面（支持 `?scope=` 参数） |
| `/wechat` | GET | 微信公众号版 |
| `/archive/{date}` | GET | 历史资讯（格式：2026-04-26） |
| `/api/refresh` | GET/POST | 手动触发采集（支持 `?scope=` 局部刷新） |
| `/api/news/today` | GET | 今日资讯 JSON |
| `/api/news/{date}` | GET | 指定日期资讯 JSON |
| `/api/status` | GET | 服务状态（采集中/完成/可用日期列表） |
