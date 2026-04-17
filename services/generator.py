"""
页面生成器 - 将采集的资讯渲染为HTML文件
统一模板 + CSS 变量主题切换
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from config import OUTPUT_DIR, TEMPLATE_DIR, THEME

logger = logging.getLogger(__name__)

# Jinja2 环境
_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _get_date_str() -> tuple[str, str]:
    """获取当前日期字符串 (显示用, 文件名用)"""
    now = datetime.now()
    display = now.strftime("%Y年%m月%d日")
    filename = now.strftime("%Y-%m-%d")
    return display, filename


def render_page(articles: list, output_type: str = "web", theme: str = None) -> Path:
    """
    渲染资讯页面并保存到output目录

    Args:
        articles: Article对象列表
        output_type: "web" 或 "wechat"
        theme: 主题名称 (notion/linear/apple)，None 时使用 config.THEME

    Returns:
        生成的HTML文件路径
    """
    date_display, date_file = _get_date_str()
    t = theme or THEME

    template_name = "wechat.html" if output_type == "wechat" else "magazine.html"
    template = _jinja_env.get_template(template_name)

    html = template.render(
        articles=articles,
        date_str=date_display,
        theme=t if output_type == "web" else "notion",
        loading=False,
    )

    # 保存文件
    suffix = "" if output_type == "web" else "_wechat"
    theme_suffix = f"_{t}" if output_type == "web" else ""
    output_path = OUTPUT_DIR / f"{date_file}{suffix}{theme_suffix}.html"
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"页面已生成: {output_path}")

    return output_path


def render_both(articles: list) -> tuple[Path, Path]:
    """同时生成Web版本和微信公众号版本"""
    web_path = render_page(articles, "web")
    wechat_path = render_page(articles, "wechat")
    return web_path, wechat_path


def save_articles_json(articles: list) -> Path:
    """保存原始文章数据为JSON（便于后续API查询）"""
    _, date_file = _get_date_str()
    json_path = OUTPUT_DIR / f"{date_file}.json"

    data = []
    for a in articles:
        data.append({
            "title": a.title,
            "url": a.url,
            "summary": a.summary,
            "source_name": a.source_name,
            "image_url": a.image_url,
            "published": a.published,
            "published_ts": a.published_ts,
            "content_quality": a.content_quality,
        })

    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"JSON数据已保存: {json_path}")
    return json_path


def load_articles_json(date_str: str = None) -> list[dict]:
    """加载指定日期的文章JSON数据"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    json_path = OUTPUT_DIR / f"{date_str}.json"
    if not json_path.exists():
        return []

    return json.loads(json_path.read_text(encoding="utf-8"))
