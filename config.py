"""
智能仓储每日简讯 - 配置文件
"""
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# 确保输出目录存在
OUTPUT_DIR.mkdir(exist_ok=True)

# 服务配置
HOST = "0.0.0.0"
PORT = 8088

# 定时任务配置
SCHEDULE_HOUR = 7    # 每天早上7点
SCHEDULE_MINUTE = 0

# 资讯数量
MAX_ARTICLES = 20
SUMMARY_MAX_LENGTH = 200  # 摘要最大字符数
MAX_ARTICLE_AGE_DAYS = 15 # 只选取15天内的文章

# HTTP 请求配置
REQUEST_TIMEOUT = 15       # 秒
MAX_CONCURRENT = 8         # 最大并发请求数
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
