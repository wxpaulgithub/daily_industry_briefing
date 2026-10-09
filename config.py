"""
智能仓储每日简讯 - 配置文件
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env", override=False)
OUTPUT_DIR = BASE_DIR / "output"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
RUNTIME_DIR = BASE_DIR / "runtime"
CONFIG_DATA_DIR = BASE_DIR / "config_data"

# Cookie 文件支持环境变量覆盖，便于挂载到源码目录外（如上级目录）
# 例如：COOKIE_FILE_PATH=/runtime/cookie.txt
_cookie_file_env = (os.getenv("COOKIE_FILE_PATH") or "").strip()
if _cookie_file_env:
    COOKIE_FILE = Path(_cookie_file_env)
else:
    COOKIE_FILE = RUNTIME_DIR / "cookie.txt"

# 确保输出目录存在
OUTPUT_DIR.mkdir(exist_ok=True)
RUNTIME_DIR.mkdir(exist_ok=True)
CONFIG_DATA_DIR.mkdir(exist_ok=True)
COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)

# 公众号来源配置文件（支持环境变量覆盖）
# 示例：WECHAT_SOURCES_FILE_PATH=/runtime/wechat_sources.json
_wechat_sources_env = (os.getenv("WECHAT_SOURCES_FILE_PATH") or "").strip()
if _wechat_sources_env:
    WECHAT_SOURCES_FILE = Path(_wechat_sources_env)
else:
    WECHAT_SOURCES_FILE = CONFIG_DATA_DIR / "wechat_sources.json"
WECHAT_SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)

# 服务配置
HOST = "0.0.0.0"
PORT = 8088
# 站点公开 URL（用于微信/QQ 分享卡片中的 og:image 绝对路径）
# 部署到云端后通过环境变量设置为实际域名，如：SITE_URL=https://news.yourdomain.com
SITE_URL = (os.getenv("SITE_URL") or "").strip().rstrip("/") or f"http://localhost:{PORT}"

# 定时任务配置
SCHEDULE_HOUR = 7    # 每天早上7点
SCHEDULE_MINUTE = 0
OPPORTUNITY_SCHEDULE_HOUR = int(os.getenv("OPPORTUNITY_SCHEDULE_HOUR") or "7")
OPPORTUNITY_SCHEDULE_MINUTE = int(os.getenv("OPPORTUNITY_SCHEDULE_MINUTE") or "20")

# 资讯数量
MAX_ARTICLES = 24
NATIONAL_SOURCE_MAX_COUNT = max(1, int(os.getenv("NATIONAL_SOURCE_MAX_COUNT") or "6"))
NATIONAL_UNKNOWN_DATE_MAX_COUNT = max(0, int(os.getenv("NATIONAL_UNKNOWN_DATE_MAX_COUNT") or "4"))
SUMMARY_MAX_LENGTH = 200  # 摘要最大字符数
MAX_ARTICLE_AGE_DAYS = 15 # 只选取15天内的文章

# 范围切换配置
# local: 本地资讯（本地项目/本地政策/本地投资）
# wechat: 公众号资讯（配置账号池）
# discover: 发现资讯（知乎/B站）
# national: 国内行业资讯（默认）
DEFAULT_SCOPE = "national"

# 本地关键词（无锡）
LOCAL_REGION_KEYWORDS = [
    "无锡", "无锡市", "新区", "新吴区", "锡山区", "惠山区", "滨湖区",
]

# 本地页展示上限（允许少于该值，不强行补满）
MAX_ARTICLES_LOCAL = 12

# 发现页展示上限（知乎/B站等辅助发现源）
MAX_ARTICLES_DISCOVER = 18
# 公众号页展示上限
MAX_ARTICLES_WECHAT = 18
MAX_OPPORTUNITIES = int(os.getenv("MAX_OPPORTUNITIES") or "30")

# 本地项目意图关键词（至少命中一项）
LOCAL_INTENT_KEYWORDS = [
    "引进项目", "最新项目", "重大项目", "招商引资", "项目签约",
    "签约仪式", "项目落地", "项目开工", "项目投产", "开工建设",
    "新建厂房", "扩建", "增资扩产", "产业园", "物流园",
]

# 本地工业/物流场景关键词（至少命中一项）
LOCAL_INDUSTRY_KEYWORDS = [
    "仓储", "物流", "供应链", "物流中心", "仓储中心",
    "智能制造", "自动化", "数字化改造", "技改", "产线",
    "工厂", "厂房", "立库", "堆垛机", "WMS", "WCS", "AGV", "AMR",
]

# 本地无关内容硬排除（命中即过滤）
LOCAL_EXCLUDE_KEYWORDS = [
    "拆迁", "征收", "安置", "回迁",
    "黄金", "珠宝", "金价", "首饰",
    "楼市", "房产", "家装", "汽车置换", "以旧换新",
    "演唱会", "文旅", "美食探店", "培训班",
    "航班", "机场", "机票", "高铁", "列车", "客运",
    "足球", "中超", "中甲", "比赛", "联赛", "体育赛事", "马拉松", "赛程",
    "音乐节", "观影", "票务", "旅游攻略", "景区",
    "人形机器人", "具身机器人", "机器狗", "四足机器人",
]

# 本地公众号白名单（先做第一批，可持续扩充）
LOCAL_WECHAT_WHITELIST = [
    "无锡发布",
    "无锡日报",
    "无锡观察",
    "无锡高新区",
    "无锡经开区",
    "新吴发布",
    "锡山发布",
    "惠山发布",
    "滨湖发布",
    "梁溪发布",
    "江阴发布",
    "宜兴发布",
    "无锡商务",
    "无锡工信",
    "无锡发改",
    "无锡招商",
]

# HTTP 请求配置
REQUEST_TIMEOUT = 15       # 秒
MAX_CONCURRENT = 8         # 最大并发请求数
SKILL_FETCH_TIMEOUT_SECONDS = 45      # 单个 Skill 全量抓取超时（秒）
SKILL_CACHE_TTL_SECONDS = 1800        # Skill 结果缓存时间（秒，默认 30 分钟）
SKILL_FAILURE_THRESHOLD = 2           # 连续失败阈值，达到后进入冷却
SKILL_FAILURE_COOLDOWN_SECONDS = 900  # Skill 冷却时间（秒，默认 15 分钟）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 图片配置
IMAGE_MAX_WIDTH = 800      # 图片最大宽度（像素）
IMAGE_QUALITY = 85         # JPEG 压缩质量
DOWNLOAD_IMAGES = True     # 是否下载图片到本地

# 页面主题配置
# 可选值: "notion", "apple", "linear"
THEME = "notion"


def _env_bool(name: str, default: bool) -> bool:
    """从环境变量读取布尔值"""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# 知乎抓取配置
# 使用登录态 HTTP + Bing 兜底方案，无需无头浏览器
# 公众号搜狗兜底（默认关闭，优先使用 RSS 主链路）
USE_SOGOU_WECHAT_FALLBACK = _env_bool("USE_SOGOU_WECHAT_FALLBACK", False)
# 建议通过环境变量注入，不要写死到代码库
ZHIHU_COOKIE = (os.getenv("ZHIHU_COOKIE") or "").strip()
# 知乎 Cookie 有效性检测间隔（分钟）
ZHIHU_COOKIE_CHECK_INTERVAL_MINUTES = float(os.getenv("ZHIHU_COOKIE_CHECK_INTERVAL_MINUTES") or "360")
# 知乎 Cookie 持续失效多久后推送告警（分钟）
ZHIHU_COOKIE_ALERT_THRESHOLD_MINUTES = float(os.getenv("ZHIHU_COOKIE_ALERT_THRESHOLD_MINUTES") or "1440")
# B站可选 Cookie（优先从 runtime/cookie.txt 读取）
BILIBILI_COOKIE = (os.getenv("BILIBILI_COOKIE") or "").strip()

# ===== RSS 授权失效告警配置 =====
# 企业微信机器人 Webhook URL（群聊 → 添加机器人 → 复制 Webhook 地址）
WECOM_WEBHOOK_URL = (os.getenv("WECOM_WEBHOOK_URL") or "").strip()
# 企业微信机器人 text 消息提醒对象。多个值用英文逗号分隔，@all 可提醒所有人
WECOM_MENTIONED_LIST = (os.getenv("WECOM_MENTIONED_LIST") or "").strip()
# 企业微信机器人手机号提醒对象。多个手机号用英文逗号分隔
WECOM_MENTIONED_MOBILE_LIST = (os.getenv("WECOM_MENTIONED_MOBILE_LIST") or "").strip()
# Server酱 SendKey（https://sct.ftqq.com/ 登录后获取）
SERVERCHAN_KEY = (os.getenv("SERVERCHAN_KEY") or "").strip()
# 告警冷却时间（小时），同一类告警在此时间段内不重复发送
ALERT_COOLDOWN_HOURS = int(os.getenv("ALERT_COOLDOWN_HOURS") or "6")
# RSS 授权健康检查间隔（小时），独立于每日简讯采集
RSS_AUTH_HEALTH_CHECK_INTERVAL_HOURS = float(os.getenv("RSS_AUTH_HEALTH_CHECK_INTERVAL_HOURS") or "8")
# RSS 全员静默阈值（小时），所有源超过此时间无更新则判定失效
RSS_SILENCE_THRESHOLD_HOURS = float(os.getenv("RSS_SILENCE_THRESHOLD_HOURS") or "48")
# 关键词命中时，本轮正常文章数达到该值即视为 feed 健康，将关键词命中判为误报（不发送告警）
# 原理：授权失效时 we-mp-rss 会退化为只返回占位提示项，正常文章数为 0；
# 若仍能抓到大量正常文章，说明授权正常，关键词命中通常是某篇文章标题误含“扫码”等字样。
RSS_KEYWORD_HEALTHY_MIN_ARTICLES = int(os.getenv("RSS_KEYWORD_HEALTHY_MIN_ARTICLES") or "5")
# we-mp-rss 授权管理中的 token 预计到期时间接口，返回 JSON 即可
WERSS_TOKEN_EXPIRY_URL = (os.getenv("WERSS_TOKEN_EXPIRY_URL") or "").strip()
# 可选鉴权头，例如 Bearer xxx 或 AK-SK xxx:yyy
WERSS_TOKEN_EXPIRY_AUTHORIZATION = (os.getenv("WERSS_TOKEN_EXPIRY_AUTHORIZATION") or "").strip()
# token 预计到期预警阈值（小时），默认 12h 和 3h
WERSS_TOKEN_WARNING_HOURS = [
    float(x.strip())
    for x in (os.getenv("WERSS_TOKEN_WARNING_HOURS") or "12,3").split(",")
    if x.strip()
]
# 告警状态文件路径
ALERT_STATUS_FILE = RUNTIME_DIR / "alert_status.json"

