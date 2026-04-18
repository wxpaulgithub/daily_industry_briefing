# 智能仓储每日简讯

自动采集工业/制造业领域资讯，以科技杂志风格排版为 Web 页面展示。支持多主题切换和微信公众号兼容输出。

## 项目结构

```
资讯杂志/
├── app.py                  # FastAPI Web 服务入口
├── fetch.py                # 独立采集脚本（不启动服务）
├── config.py               # 全局配置
├── requirements.txt        # Python 依赖
├── services/
│   ├── fetcher.py          # 公共基类 + 过滤/去重/精选逻辑
│   ├── generator.py        # HTML 页面生成器（Jinja2）
│   └── skills/             # 可插拔数据源 Skill 目录
│       ├── __init__.py     # Skill 注册表
│       └── toutiao.py      # 今日头条 Skill
├── templates/
│   ├── magazine.html       # Web 版模板（移动端优先，3种主题）
│   └── wechat.html         # 微信公众号版模板（内联 CSS）
├── static/
│   ├── logo.png            # Logo（深色背景用）
│   └── logo_w.png          # Logo 白色版（浅色背景用）
├── output/                 # 生成的每日 HTML 文件
├── venv/                   # Python 虚拟环境
```

## 快速开始

### 1. 安装

```bash
cd "Z:\home\Drive\劢世达\Logo素材\上线网页\资讯杂志"

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 采集资讯

```bash
# 方式一：独立采集（推荐，只生成页面不启动服务）
python fetch.py

# 方式二：启动 Web 服务（含自动采集 + 定时任务）
python app.py
```

采集完成后，`output/` 目录会生成以下文件：

| 文件 | 说明 |
|------|------|
| `2026-04-17_notion.html` | Web 版 - 纸主题（默认） |
| `2026-04-17_apple.html` | Web 版 - 简主题 |
| `2026-04-17_linear.html` | Web 版 - 深主题 |
| `2026-04-17_wechat.html` | 微信公众号版，内联 CSS，可直接复制粘贴到公众号编辑器 |
| `2026-04-17.json` | 原始数据 JSON，供 API 查询 |

### 3. 查看结果

- **本地查看**：直接双击 `output/` 下的 `.html` 文件
- **Web 服务**：启动 `python app.py` 后访问 `http://localhost:8088`
- **手机查看**：确保手机与电脑在同一局域网，访问 `http://<电脑IP>:8088`
- **主题切换**：页面上方有「纸 / 简 / 深」切换按钮，或通过 URL 参数 `?theme=notion|apple|linear`

---

## 数据源 Skill 架构

系统采用可插拔的 Skill 架构，每个数据源是一个独立的 Skill，所有 Skill 并发采集后统一过滤去重精选。

### 架构说明

```
fetch_all_news()
    ├── Skill 1: ToutiaoSkill   → fetch_all() → 60 条
    ├── Skill 2: (未来扩展)      → fetch_all() → ...
    └── Skill 3: (未来扩展)      → fetch_all() → ...
         │
         ▼ 合并所有结果
    filter_relevant()  → 排除无关内容
         │
         ▼
    deduplicate()      → 标题相似度去重
         │
         ▼
    select_articles()  → 打分精选 → 最终 15 条（可配置）
```

### Skill 基类

所有数据源继承 `services/fetcher.py` 中的 `NewsSkill` 基类，必须实现 3 个属性/方法：

```python
from services.fetcher import NewsSkill, Article, normalize_summary, clean_title

class MyNewSkill(NewsSkill):

    @property
    def name(self) -> str:
        """Skill 名称，用于日志"""
        return "我的数据源"

    @property
    def search_queries(self) -> list[dict]:
        """搜索关键词列表"""
        return [
            {"keyword": "关键词A 关键词B", "label": "标签A"},
            {"keyword": "关键词C 关键词D", "label": "标签B"},
        ]

    async def fetch(self, client, keyword: str, count: int = 10) -> list[Article]:
        """从数据源获取资讯（核心方法）"""
        articles = []
        # ... 发起请求、解析数据 ...
        articles.append(Article(
            title="标题",
            url="https://...",
            summary="摘要",
            source_name="来源",
            image_url="https://...jpg",
            published="2026-04-17 10:00",
            published_ts=1744864800.0,
        ))
        return articles
```

### 新增数据源（3 步）

以添加「36氪 RSS」为例：

**第 1 步：创建 Skill 文件**

在 `services/skills/` 下新建 `kr36.py`：

```python
"""36氪 RSS 数据源 Skill"""
import feedparser
from services.fetcher import NewsSkill, Article, normalize_summary, clean_title

class Kr36Skill(NewsSkill):

    @property
    def name(self) -> str:
        return "36氪"

    @property
    def search_queries(self) -> list[dict]:
        return [{"keyword": "智能制造 工业自动化 机器人", "label": "36氪工业"}]

    async def fetch(self, client, keyword: str, count: int = 10) -> list[Article]:
        articles = []
        resp = await client.get("https://36kr.com/feed")
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:count]:
            title = entry.get("title", "")
            text = (title + entry.get("summary", "")).lower()
            # 按关键词过滤，只保留工业相关
            keywords = keyword.split()
            if not any(kw in text for kw in keywords):
                continue
            articles.append(Article(
                title=clean_title(title),
                url=entry.get("link", ""),
                summary=normalize_summary(entry.get("summary", "")),
                source_name="36氪",
            ))
        return articles
```

**第 2 步：注册 Skill**

编辑 `services/skills/__init__.py`，添加一行：

```python
from services.skills.toutiao import ToutiaoSkill
from services.skills.kr36 import Kr36Skill          # 新增

SKILLS = [
    ToutiaoSkill(),
    Kr36Skill(),                                    # 新增
]
```

**第 3 步：验证**

```bash
python fetch.py
```

查看日志中是否出现 `[36氪] 采集完成` 即可确认生效。

### 当前已注册 Skill

| Skill | 文件 | 数据源 | 特点 |
|-------|------|--------|------|
| ToutiaoSkill | `skills/toutiao.py` | 今日头条搜索 API | 国内可用，图片覆盖率 86%+ |

---

## 调整资讯内容

资讯内容由三层控制：**Skill 搜索关键词**、**内容过滤规则**、**精选参数**。

### 关键词调整速查

| 想做什么 | 改哪个文件 | 改什么 |
|----------|-----------|--------|
| 多抓 / 少抓某个方向的内容 | `services/skills/toutiao.py` | `search_queries` — 搜索关键词，决定从数据源拉回多少条原始结果 |
| 控制最终内容的相关性门槛 | `services/fetcher.py` | `REQUIRED_KEYWORDS` — 白名单，标题或摘要必须命中至少一个才保留 |
| 某些无关内容混进来了 | `services/fetcher.py` | `EXCLUDE_KEYWORDS` — 黑名单，标题命中任何一个就直接丢弃 |

### 修改搜索关键词

编辑对应 Skill 文件中的 `search_queries` 属性。例如编辑 `services/skills/toutiao.py`：

```python
@property
def search_queries(self) -> list[dict]:
    return [
        {"keyword": "智能制造 工业自动化 智能仓储 立库 堆垛机", "label": "智能制造"},
        {"keyword": "智能工厂 数字化工厂", "label": "智能工厂"},
        {"keyword": "立库招投标 仓储设备 智能仓库 中标", "label": "行业信息"},
        {"keyword": "制造业 数字化转型 工业互联网", "label": "数字化"},
        {"keyword": "工业4.0 数字孪生 MES系统", "label": "工业技术"},
        {"keyword": "工业自动化展 智能制造展 物流展", "label": "工业展览"},
    ]
```

调整建议：

- **增加方向**：添加新的 `{"keyword": "...", "label": "..."}` 条目即可
- **缩小范围**：关键词越精确，结果越精准。例如 `"AGV 搬运机器人"` 比 `"机器人"` 更精准
- **扩大范围**：用空格分隔多个关键词，搜索结果会包含任意一个
- **每组建议 2-5 个词**，每组 API 最多返回 10 条

### 修改内容过滤规则

编辑 `services/fetcher.py`：

**必选关键词**（`REQUIRED_KEYWORDS`）- 文章标题或摘要必须至少命中一个：

```python
REQUIRED_KEYWORDS = [
    "制造", "工厂", "产线", "生产线", "车间", "加工",
    "自动化", "智能", "数字化", "数智", "信息化",
    "仓储", "仓库", "立库", "堆垛机", "AGV", "物流",
    "工业", "PLC", "MES", "WMS", "SCADA", "ERP",
    "机器人", "机械臂", "协作机器",
    "装备", "设备", "仪器", "传感器",
    "项目", "中标", "招标", "签约", "投产",
    "展会", "博览会", "论坛", "峰会",
    "数字孪生", "工业互联网", "工业4.0", "仿真",
]
```

**排除关键词**（`EXCLUDE_KEYWORDS`）- 标题命中任何一个则直接丢弃：

```python
EXCLUDE_KEYWORDS = [
    "村支书", "村干部", "纪委", "反腐", "落马", "违纪",
    "贪腐", "受贿", "举报", "巡视", "处分",
    "房价", "楼市", "股票", "A股", "涨停", "跌停",
    "明星", "综艺", "娱乐", "电影", "电视剧",
    "高考", "招生", "学区", "中考",
    "疫情", "核酸", "疫苗",
    "贪污", "官员", "书记", "局长",
]
```

### 修改精选参数

编辑 `config.py`：

```python
MAX_ARTICLES = 15          # 最终精选文章数量
SUMMARY_MAX_LENGTH = 200   # 摘要最大字符数
MAX_ARTICLE_AGE_DAYS = 15  # 只选取15天内的文章
```

编辑 `services/fetcher.py` 中的 `score_article` 函数可调整打分权重（时效性、图片、来源可靠性等）。

---

## 本地服务

### 启动

```bash
venv\Scripts\activate
python app.py
```

服务默认监听 `0.0.0.0:8088`。

### 可用端点

| 路径 | 说明 |
|------|------|
| `http://localhost:8088/` | 今日资讯页面（默认主题） |
| `http://localhost:8088/?theme=apple` | 今日资讯页面（指定主题：notion/apple/linear） |
| `http://localhost:8088/wechat` | 微信公众号版 |
| `http://localhost:8088/archive/2026-04-17` | 历史某天的资讯 |
| `http://localhost:8088/archive/2026-04-17?theme=linear` | 历史资讯（指定主题） |
| `http://localhost:8088/api/refresh` | 手动触发重新采集 |
| `http://localhost:8088/api/news/today` | 今日资讯 JSON 数据 |
| `http://localhost:8088/api/news/2026-04-17` | 指定日期资讯 JSON 数据 |
| `http://localhost:8088/api/status` | 服务状态 |

### 修改端口

编辑 `config.py`：

```python
PORT = 8088  # 改为你需要的端口
```

---

## 页面主题系统

Web 版模板支持 3 种视觉主题，通过 CSS 变量实现即时切换：

### 主题一览

| 主题 | 名称 | 风格 | 配色特点 |
|------|------|------|----------|
| `notion` | 纸 | 典雅复古 | 暖白底 `#F7F5F0`，爱马仕橘红 `#E04825`，衬线字体，微复古滤镜 |
| `apple` | 简 | 纯粹极简 | 纯白底 `#FFFFFF`，科技蓝 `#0066CC`，大圆角卡片 |
| `linear` | 深 | 沉浸先锋 | 纯黑底 `#000000`，霓虹电紫 `#6C5CE7`，暗色沉浸 |

- **默认主题**：在 `config.py` 中设置 `THEME = "notion"`
- **URL 切换**：访问 `/?theme=apple` 或 `/archive/2026-04-17?theme=linear`
- **页面切换**：页头右侧有「纸 / 简 / 深」三个按钮，点击即时切换

---

## 手动更新资讯

### 命令行方式

```bash
cd "Z:\home\Drive\劢世达\Logo素材\上线网页\资讯杂志"
venv\Scripts\python fetch.py
```

运行完毕后 `output/` 目录会生成当天的 HTML 文件。

### Web 方式

服务运行期间，访问 `http://localhost:8088/api/refresh` 触发重新采集。

---

## 自动定时更新

### 方案一：Windows 任务计划程序（推荐）

1. 按 `Win+R`，输入 `taskschd.msc` 打开任务计划程序
2. 右侧点击「创建基本任务」
3. 名称填写 `智能仓储每日简讯采集`
4. 触发器选择「每天」，时间设为 `07:00`
5. 操作选择「启动程序」，填写：
   - 程序或脚本：`Z:\home\Drive\劢世达\Logo素材\上线网页\资讯杂志\venv\Scripts\python.exe`
   - 添加参数：`fetch.py`
   - 起始于：`Z:\home\Drive\劢世达\Logo素材\上线网页\资讯杂志`
6. 完成

注意事项：
- 勾选「不管用户是否登录都要运行」可确保开机自动执行
- 如果电脑在设定时间处于关机/休眠状态，任务会在下次开机时补执行（需在任务属性中勾选）

### 方案二：服务内置定时任务

使用 `python app.py` 启动服务时，内置的 APScheduler 会每天自动采集。时间在 `config.py` 中配置：

```python
SCHEDULE_HOUR = 7    # 小时
SCHEDULE_MINUTE = 0  # 分钟
```

此方案需要服务持续运行。

---

## 云端部署

### 方式一：部署到云服务器（Linux）

```bash
# 1. 上传项目到服务器
scp -r 资讯杂志/ user@your-server:/opt/industry_briefing/

# 2. SSH 登录服务器
ssh user@your-server

# 3. 安装依赖
cd /opt/industry_briefing
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. 测试采集
python fetch.py

# 5. 用 systemd 设置开机自启
sudo cat > /etc/systemd/system/industry-briefing.service << 'EOF'
[Unit]
Description=Industry Briefing Service
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/industry_briefing
ExecStart=/opt/industry_briefing/venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable industry-briefing
sudo systemctl start industry-briefing
```

访问 `http://<服务器IP>:8088` 查看效果。

### 方式二：部署到云服务器（Docker）

```dockerfile
# Dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p output
EXPOSE 8088
CMD ["python", "app.py"]
```

```bash
# 构建并运行
docker build -t industry-briefing .
docker run -d -p 8088:8088 -v $(pwd)/output:/app/output industry-briefing
```

### 方式三：配合 Nginx 反向代理

如果服务器已有 Nginx，可添加反向代理配置：

```nginx
server {
    listen 80;
    server_name news.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # 直接提供静态 HTML（不经过 Python）
    location /static/ {
        alias /opt/industry_briefing/output/;
    }
}
```

### 方式四：纯静态部署（最简单）

由于生成的 HTML 是纯静态文件，可以：

1. 运行 `python fetch.py` 生成页面
2. 将 `output/` 目录下的 `.html` 文件上传到任意静态托管（OSS、CDN、GitHub Pages 等）
3. 用户直接访问静态 URL

此方案不需要服务器运行 Python，成本最低。

---

## 微信公众号发布

### 手动发布

1. 运行 `python fetch.py` 或访问 `http://localhost:8088/wechat`
2. 浏览器打开 `_wechat.html` 文件
3. 全选页面内容（Ctrl+A），复制
4. 登录微信公众号后台，新建图文，粘贴
5. 根据需要微调排版后发布

注意事项：
- 微信公众号编辑器会自动过滤外部图片域名，首次发布时可能需要手动替换图片
- 微信要求图片必须在其图床上，可通过公众号后台「素材管理」上传后替换 URL
- 内联 CSS 兼容微信公众号编辑器，无需额外样式

### 自动发布（进阶）

通过微信公众号素材管理 API 可实现自动创建草稿。需要：

1. 在公众号后台获取 AppID 和 AppSecret
2. 调用素材上传接口将图片上传到微信图床
3. 替换 HTML 中的图片 URL
4. 调用草稿接口创建文章

此功能需额外开发，当前版本未集成。

---

## 技术架构

| 组件 | 技术 | 用途 |
|------|------|------|
| 数据源 | 可插拔 Skill 架构 | 每个数据源独立实现，统一注册 |
| Web 框架 | FastAPI + Uvicorn | 轻量 API 服务 |
| 模板引擎 | Jinja2 | HTML 渲染 |
| 定时任务 | APScheduler | 每日自动采集 |
| 页面设计 | 纯 CSS + CSS 变量主题 | 移动端优先，3 种视觉主题 |

### 主题配色详情

#### 纸 (notion) - 默认主题

| 用途 | 色值 | 说明 |
|------|------|------|
| 页面背景 | `#F7F5F0` | 暖白纸张底色 |
| 标题文字 | `#1A1A1A` | 深灰，衬线字体 |
| 摘要文字 | `#56534E` | 中灰 |
| 来源/日期 | `#95918A` | 浅灰 |
| 强调色 | `#E04825` | 爱马仕橘红 |
| 分隔线 | `#E0DDD6` | 极浅灰 |
| 品牌色 | `#4AB0B0` | 页脚装饰 |

#### 简 (apple) - 极简主题

| 用途 | 色值 | 说明 |
|------|------|------|
| 页面背景 | `#FFFFFF` | 纯白 |
| 标题文字 | `#666666` | 中灰 |
| 强调色 | `#0066CC` | 科技蓝 |
| 卡片圆角 | `20px` | 大圆角 |

#### 深 (linear) - 暗色主题

| 用途 | 色值 | 说明 |
|------|------|------|
| 页面背景 | `#000000` | 纯黑 |
| 标题文字 | `#FAFAFA` | 亮白 |
| 强调色 | `#6C5CE7` | 霓虹电紫 |
| 卡片圆角 | `16px` | 中等圆角 |
