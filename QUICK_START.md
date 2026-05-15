# 快速上手指南

本文档帮助新参与者快速理解"智能仓储每日简讯"项目的信息采集机制，以及如何基于本项目进行本地化二次开发。

---

## 一、项目是做什么的

一句话：**自动从多个渠道采集工业/仓储/物流领域的资讯，生成一份科技杂志风格的 Web 页面**。

输出物：
- `output/{日期}.html` -- 可浏览器访问的 Web 版（4 个 Tab：国内、本地、公众号、发现）
- `output/{日期}_wechat.html` -- 微信公众号版（可直接复制粘贴到公众号编辑器）
- `output/{日期}.json` -- 原始数据 JSON
- `output/images/` -- 本地化的文章配图

---

## 二、先搞清楚：哪些开箱即用、哪些需要额外运维

这是二次开发前**最重要**的信息。本项目的 4 个页面 Tab 中，各数据源的"即插即用"程度差异很大：

### 2.1 各数据源的开箱状态

| 页面 Tab | 数据源 | 能否开箱即用 | 需要什么额外工作 | 运维频率 |
|----------|--------|------------|----------------|---------|
| 国内 | 今日头条搜索 | 可以 | 无 | 无 |
| 国内 | 政策标准 RSS | 可以 | 无 | 无 |
| 国内 | 行业媒体 RSS | 可以 | 无 | 无 |
| 国内 | 展会协会 RSS | 可以 | 无 | 无 |
| 国内 | 招投标公告 | 可以 | 无 | 无 |
| 本地 | 本地项目（头条搜索） | 可以 | 改 `config.py` 中的 `LOCAL_REGION_KEYWORDS` 为你的城市 | 无 |
| 公众号 | 公众号 RSS | **不能** | 需要部署并维护 `we-mp-rss` 上游服务（见下方） | **每 3-5 天需扫码续期** |
| 公众号 | 搜狗微信搜索 | 可以（默认关闭） | 设置 `USE_SOGOU_WECHAT_FALLBACK=true` | 偶尔触发反爬 |
| 发现 | 知乎 | 部分可以 | 无 Cookie 可降级运行，但质量下降 | Cookie **几天到几周**需更新 |
| 发现 | B站 | 部分可以 | 无 Cookie 可运行，有 Cookie 更稳定 | Cookie 较少过期 |

### 2.2 公众号 RSS 的特殊依赖（最大的运维负担）

**公众号页是本项目内容最丰富的 Tab，但也是运维成本最高的。**

它依赖一个独立的上游服务 **[we-mp-rss](https://github.com/qinli-jian/we-mp-rss)**（以下简称 WERSS），作用是将微信公众号文章转为标准 RSS Feed。你需要单独部署这个服务，不能跳过。

**为什么需要它？** 微信公众号文章没有公开的搜索 API，也不能直接通过 URL 订阅。WERSS 通过模拟微信后台登录来抓取文章，然后以 RSS 格式输出。

**部署步骤**：
1. Docker 部署 WERSS 服务（详见其官方文档）
2. 在 WERSS 管理界面添加你要订阅的公众号
3. 将 WERSS 生成的 RSS 地址填入 `config_data/wechat_sources.json` 的 `rss_url` 字段

**核心运维问题 -- 授权会过期**：

WERSS 通过微信扫码登录获取授权，但微信的 Session 有效期只有约 **80 小时（3-5 天）**。过期后 RSS Feed 停止更新，公众号页将无新内容。

项目已内置 **独立的公众号 RSS 授权健康检查**。如果配置了 `we-mp-rss` 授权管理中的 token 预计到期时间接口，系统会：

- 在距离到期 **12 小时**时发送第一次预警
- 在距离到期 **3 小时**时发送第二次预警
- 根据距离到期时间动态调整 RSS 异常检测频率：
  `>24h 每12h`、`24h~6h 每6h`、`6h内 每3h`、`已过期 每3h`

如果没有配置 token 到期接口，系统会回退到 `RSS_AUTH_HEALTH_CHECK_INTERVAL_HOURS` 固定周期检查。无论采用哪种模式，只要检测到 RSS 关键字异常、持续静默或整体陈旧，都会通过企业微信机器人或 Server酱发送确认告警。

| 维持方式 | 自动化程度 | 说明 |
|---------|----------|------|
| 多账号自动切换 | 半自动 | WERSS 内置功能。绑定至少 2 个公众号管理员，系统每天自动切换账号延长有效期，可将续期间隔延长到数周。但遇到微信风控（滑块验证、异地校验）仍需人工扫码。 |
| 手动扫码 | 全人工 | 直接在 WERSS 管理界面重新扫码登录。适合个人低频使用。 |
| ADB 自动扫码 | 全自动 | 需要两台安卓设备/云手机，部署 `we-mp-auto-scan` 项目。通过 ADB 模拟扫码动作，实现无人值守。仅当微信被登出或设备重启才需人工。 |

**如果你不想维护 WERSS**：
- 公众号页可以改用搜狗搜索通道（设置 `USE_SOGOU_WECHAT_FALLBACK=true`），但内容质量和覆盖率不如 RSS
- 或者直接在 `services/skills/__init__.py` 中注释掉 `WeChatRssSkill`，去掉公众号页

### 2.3 知乎/B站 Cookie 的运维成本

知乎搜索 API 需要登录态 Cookie，没有的话会降级到 HTML 解析 + Bing 兜底（内容质量明显下降，链接可能不完整）。

| 站点 | Cookie 获取方式 | 有效期 | 失效后影响 |
|------|---------------|--------|----------|
| 知乎 | 浏览器 F12 → Network → 任意请求 → 复制 Cookie 头 | 几天到几周不等 | 降级到 HTML+Bing 兜底，内容减少 |
| B站 | 同上 | 较长（月级） | 影响较小，匿名也能用 |

更新方法：
```bash
# 交互式引导更新（推荐）
python scripts/update_cookie.py --site zhihu
# 查看当前 Cookie 状态
python scripts/read_cookie_file.py
```

Cookie 写入 `runtime/cookie.txt`，修改后无需重启服务。

补充说明：系统会按 `ZHIHU_COOKIE_CHECK_INTERVAL_MINUTES` 周期独立检测知乎登录态，默认每 6 小时校验一次 `https://www.zhihu.com/api/v4/me`。如果 Cookie 持续失效超过 `ZHIHU_COOKIE_ALERT_THRESHOLD_MINUTES`（默认 1 天），会自动通过企业微信机器人或 Server酱推送告警；在此期间发现页仍会降级到 HTML + Bing 兜底继续运行。

### 2.4 运维成本总结

| 运维项 | 频率 | 不做的后果 | 严重程度 |
|--------|------|----------|---------|
| 微信 WERSS 扫码续期 | 每 3-5 天（自动切换可延长到数周） | 公众号页无新内容 | 高（如果需要公众号页） |
| 知乎 Cookie 更新 | 每几天到几周 | 发现页知乎内容降级 | 中（有兜底机制） |
| B站 Cookie 更新 | 偶尔 | 影响较小 | 低 |
| 检查采集日志 | 建议每周 | 可能错过静默失败 | 低 |

**结论**：如果你只想要"国内 + 本地"两个 Tab，**零运维**就能跑。如果需要"公众号"Tab，WERSS 的维护是绕不开的。

---

## 三、信息采集是如何实现的

### 3.1 整体架构

系统采用**可插拔 Skill 架构**，核心思路是：

```
每个数据源 = 一个 Skill 类（继承 NewsSkill 基类）
所有 Skill 并发采集 → 统一过滤/去重/打分 → 按范围分别精选 → 渲染输出
```

关键文件只有 3 个：

| 文件 | 作用 |
|------|------|
| `services/fetcher.py` | NewsSkill 基类 + 过滤/去重/打分/精选的全部逻辑 |
| `services/skills/__init__.py` | Skill 注册表（在此添加/移除 Skill） |
| `services/skills/*.py` | 各数据源的具体实现（一个文件 = 一个数据源） |

### 3.2 信息流向（5 步）

```
第1步：Skill 并发抓取
  每个 Skill 按 search_queries 定义的搜索词，用 httpx 异步请求目标站点
       |
第2步：过滤（filter_relevant）
  必选关键词（REQUIRED_KEYWORDS）至少命中一个 + 排除关键词（EXCLUDE_KEYWORDS）命中即丢弃
       |
第3步：去重（deduplicate）
  按标题相似度去重，避免同一事件被多个数据源重复收录
       |
第4步：打分（score_article）
  按时效性（发布时间）、是否有图、摘要质量、来源权威度、厂商相关性等维度综合打分
       |
第5步：精选（select_articles）
  按4个范围（national / local / discover / wechat）分别精选，采用蛇形选秀保证来源多样性
```

### 3.3 数据源一览

| 数据源 | Skill 文件 | 采集方式 | 需要 Cookie |
|--------|-----------|---------|------------|
| 今日头条搜索 | `toutiao.py` | 搜索 API | 否 |
| 公众号 RSS | `wechat_rss.py` | RSS Feed | 否（需上游 we-mp-rss 服务） |
| 公众号搜狗搜索 | `wechat.py` | HTML 解析 | 否（默认关闭） |
| 知乎 | `zhihu_discover.py` | 搜索 API + HTML + Bing 兜底 | 是（可选，没有会降级） |
| B站 | `bilibili_discover.py` | 搜索 API + HTML + Bing 兜底 | 可选 |
| 招投标 | `bidding.py` | RSS + 头条搜索 | 否 |
| 政策标准 | `policy.py` | RSS | 否 |
| 行业媒体 | `industry_media.py` | RSS | 否 |
| 展会协会 | `expo_assoc.py` | RSS | 否 |
| 本地项目 | `local_projects.py` | 头条搜索（拼区域词） | 否 |

### 3.4 三层降级机制

系统对知乎、B站等有反爬的数据源设计了三层降级：

```
第1层：官方搜索 API（最佳效果，部分需 Cookie）
  | 失败
第2层：HTML 页面解析（提取内嵌的 JSON 状态数据）
  | 失败
第3层：Bing 站内搜索兜底（搜索 site:zhihu.com 等域名）
```

同时每个 Skill 被 `_run_skill_with_guard` 包裹，提供超时保护（45秒）、结果缓存（30分钟）、失败冷却（连续失败2次后冷却15分钟）等运行态保护。

### 3.5 页面生成

采集完成后，`services/generator.py` 负责：
1. 下载所有文章图片到本地（解决防盗链）
2. 用 Jinja2 模板（`templates/magazine.html`）渲染 HTML
3. 保存到 `output/` 目录

---

## 四、如何进行本地化二次开发

### 4.1 最快上手路径（5 分钟）

```bash
# 1. 创建虚拟环境
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行一次采集，看效果
python fetch.py

# 4. 查看结果
# 用浏览器打开 output/ 下当天日期的 .html 文件
```

### 4.2 最常见的改动场景

#### 场景一：改变采集的行业方向（最常见）

如果你不是做智能仓储，而是想做其他行业的资讯（如新能源、医疗器械、半导体），需要改以下内容：

**1. 改搜索关键词** -- 决定拉回什么内容

编辑各 Skill 文件的 `search_queries` 属性：

```python
# services/skills/toutiao.py
@property
def search_queries(self) -> list[dict]:
    return [
        {"keyword": "新能源 光伏 储能 锂电池", "label": "新能源"},
        {"keyword": "半导体 芯片 晶圆 封装", "label": "半导体"},
    ]
```

**2. 改过滤关键词** -- 决定保留什么内容

编辑 `services/fetcher.py` 中的两个列表：

```python
# 必选关键词（标题或摘要至少命中一个才保留）
REQUIRED_KEYWORDS = ["制造", "工厂", "储能", "光伏", "芯片", ...]

# 排除关键词（命中任何一个直接丢弃）
EXCLUDE_KEYWORDS = ["明星", "综艺", "房价", ...]
```

**3. 改本地区域** -- 决定本地页搜哪里

编辑 `config.py`：

```python
LOCAL_REGION_KEYWORDS = ["上海", "浦东", "松江", "嘉定"]
```

**4. 改公众号列表** -- 决定公众号页展示哪些账号

编辑 `config_data/wechat_sources.json`，替换为你关注的公众号。

#### 场景二：新增一个数据源

三步完成：

**第1步**：在 `services/skills/` 下创建新文件，继承 `NewsSkill`

```python
# services/skills/my_source.py
from services.fetcher import Article, NewsSkill, clean_title

class MySourceSkill(NewsSkill):
    @property
    def name(self) -> str:
        return "my_source"

    @property
    def region_scope(self) -> str:
        return "national"  # 或 "local" / "discover" / "wechat"

    @property
    def search_queries(self) -> list[dict]:
        return [
            {"keyword": "关键词1 关键词2", "label": "标签"},
        ]

    async def fetch(self, client, keyword: str, count: int = 10) -> list[Article]:
        # 在此实现你的采集逻辑
        # 返回 Article 对象列表
        return []
```

**第2步**：在 `services/skills/__init__.py` 注册

```python
from services.skills.my_source import MySourceSkill

SKILLS = [
    # ... 已有 Skill ...
    MySourceSkill(),  # 添加到列表末尾
]
```

**第3步**：运行 `python fetch.py` 测试

#### 场景三：如果新数据源是 RSS 订阅

更简单，继承 `RSSKeywordSkill` 基类即可，只需定义配置：

```python
# services/skills/my_rss.py
from services.skills.rss_generic import RSSKeywordSkill

class MyRssSkill(RSSKeywordSkill):
    skill_name = "我的RSS源"
    feed_sources = [
        {"url": "https://example.com/feed.xml", "label": "示例源"},
    ]
    include_keywords = ["关键词1", "关键词2"]
    exclude_keywords = ["排除词"]
```

基类自动处理 RSS 解析、HTML 兜底、指数退避重试、关键词过滤。

#### 场景四：改页面展示条数和样式

编辑 `config.py`：

```python
MAX_ARTICLES = 30            # 国内页最多条数
MAX_ARTICLES_LOCAL = 15      # 本地页
MAX_ARTICLES_DISCOVER = 20   # 发现页
MAX_ARTICLES_WECHAT = 20     # 公众号页
```

页面样式在 `templates/magazine.html` 中修改，支持 3 种主题（notion / apple / linear），通过 CSS 变量切换。

### 4.3 运行模式

| 方式 | 命令 | 适用场景 |
|------|------|---------|
| 独立采集 | `python fetch.py` | 采集一次就结束，适合计划任务 |
| Web 服务 | `python app.py` | 启动 HTTP 服务（含定时采集），适合持续运行 |
| Docker | `docker-compose up -d` | 云端部署 |

---

## 五、需要注意的事项

### 5.1 反爬风险

本系统通过以下策略应对反爬：
- 请求间随机延时
- 正确设置 User-Agent 和 Referer
- 搜狗通道顺序请求（不并发）+ 命中反爬后全局冷却 300 秒
- 知乎/B站三层降级 + Bing 兜底

**注意**：高频使用可能导致 IP 被目标站点封禁。建议：
- 不要过于频繁地手动刷新
- 定时任务每天运行 1-2 次即可
- 如部署在云服务器，注意出口 IP 的风控策略

### 5.2 图片防盗链

微信公众号、知乎、B站的图片 CDN 都做了 Referer 校验。系统通过以下方式解决：
- 采集时将所有图片下载到本地（`output/images/`）
- 下载时自动设置正确的 Referer 头
- 微信防盗链占位图（< 5KB）会被主动清除，回退到备用图

### 5.3 失效告警配置

项目内置了 RSS 源失效的自动检测和告警机制（详见 `wechat_rss_自动维持有效的说明.md`）。当 WERSS 授权过期导致 RSS Feed 停更时，系统可通过企业微信机器人或 Server酱推送预警和确认告警。

配置方式 -- 在部署环境设置以下环境变量：

```
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...   # 企业微信机器人
SERVERCHAN_KEY=SCT...                                                          # Server酱
ALERT_COOLDOWN_HOURS=6                                                         # 告警冷却（避免重复推送）
RSS_SILENCE_THRESHOLD_HOURS=48                                                 # RSS 静默多久触发告警
RSS_AUTH_HEALTH_CHECK_INTERVAL_HOURS=8                                         # we-mp-rss 独立健康检查周期
WERSS_TOKEN_EXPIRY_URL=http://你的-we-mp-rss/token-expiry-api                  # we-mp-rss token 预计到期时间接口
WERSS_TOKEN_WARNING_HOURS=12,3                                                 # 预计到期预警阈值（小时）
ZHIHU_COOKIE_CHECK_INTERVAL_MINUTES=360                                        # 知乎 Cookie 检查周期
ZHIHU_COOKIE_ALERT_THRESHOLD_MINUTES=1440                                      # 知乎 Cookie 持续失效多久后推送告警
```

### 5.4 Python 版本

项目使用 Python 3.10+ 语法（`list[dict]` 类型注解、`match/case` 等），请确保 Python 版本 >= 3.10。

---

## 六、二次开发所需的基础能力

### 必备能力

| 能力 | 说明 |
|------|------|
| Python 基础 | 能读懂和编写 async/await 异步代码 |
| HTTP 协议基础 | 理解 GET/POST 请求、Cookie、Referer、User-Agent 等概念 |
| JSON / HTML 解析 | 能从 API 响应或 HTML 页面中提取所需数据 |
| 基本的命令行操作 | 能运行 Python 脚本、安装依赖、查看日志 |

### 进阶能力（用于复杂二开）

| 能力 | 场景 |
|------|------|
| 浏览器开发者工具（F12） | 导出 Cookie、分析目标站点的 API 接口 |
| RSS/Atom 协议 | 新增 RSS 数据源 |
| CSS / Jinja2 模板 | 修改页面展示样式 |
| Docker | 云端部署 |
| 正则表达式 | 从非结构化 HTML 中提取数据 |
| 反爬策略知识 | 应对目标站点的风控升级 |

### 技术栈速查

本项目使用的技术栈都很轻量：

- **FastAPI** -- Web 框架（路由、API、静态文件服务）
- **httpx** -- 异步 HTTP 客户端（替代 requests，支持 async）
- **feedparser** -- RSS/Atom 解析
- **Jinja2** -- HTML 模板渲染
- **APScheduler** -- 定时任务调度
- **Pillow** -- 图片处理（压缩、调整尺寸）

这些都是 Python 生态中各自领域的标准选择，文档丰富，遇到问题容易找到答案。

---

## 七、文件修改速查表

| 想做什么 | 改哪个文件 |
|----------|-----------|
| 改头条搜索方向 | `services/skills/toutiao.py` 的 `search_queries` |
| 改公众号搜索词 | `config_data/wechat_sources.json` 的 `queries` |
| 改知乎搜索词 | `services/skills/zhihu_discover.py` 的 `search_queries` |
| 改B站搜索词 | `services/skills/bilibili_discover.py` 的 `search_queries` |
| 改招投标搜索词 | `services/skills/bidding.py` 的 `search_queries_toutiao` |
| 改本地区域 | `config.py` 的 `LOCAL_REGION_KEYWORDS` |
| 调整内容相关性 | `services/fetcher.py` 的 `REQUIRED_KEYWORDS` / `EXCLUDE_KEYWORDS` |
| 排除某些无关内容 | `services/fetcher.py` 的 `EXCLUDE_KEYWORDS` |
| 调整各页条数上限 | `config.py` 的 `MAX_ARTICLES_*` |
| 新增一个 RSS 数据源 | 在 `services/skills/` 下新建文件，继承 `RSSKeywordSkill` |
| 新增一个搜索数据源 | 在 `services/skills/` 下新建文件，继承 `NewsSkill` |
| 注册新数据源 | `services/skills/__init__.py` 的 `SKILLS` 列表 |
| 修改页面样式 | `templates/magazine.html` |
| 修改服务端口/定时 | `config.py` 的 `PORT` / `SCHEDULE_*` |
| 配置 Cookie | `runtime/cookie.txt` 或运行 `scripts/update_cookie.py` |
| 配置公众号账号 | `config_data/wechat_sources.json` |
