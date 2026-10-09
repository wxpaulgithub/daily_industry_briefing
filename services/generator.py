"""
页面生成器 - 将采集的资讯渲染为HTML文件
统一模板 + CSS 变量主题切换
"""
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader

from config import DOWNLOAD_IMAGES, IMAGE_MAX_WIDTH, IMAGE_QUALITY, OUTPUT_DIR, SITE_URL, TEMPLATE_DIR

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


def render_html(
    articles: list,
    output_type: str = "web",
    scope: str = "national",
    wechat_kind: str = "all",
    opportunities: list | None = None,
    date_display: str | None = None,
) -> str:
    """渲染 HTML 文本（不落盘）"""
    if scope == "national":
        from services.national_news import quality_score
        articles = sorted(articles, key=quality_score, reverse=True)
    date_display = date_display or _get_date_str()[0]
    template_name = "wechat.html" if output_type == "wechat" else "magazine.html"
    template = _jinja_env.get_template(template_name)
    return template.render(
        articles=articles,
        date_str=date_display,
        loading=False,
        scope=scope,
        wechat_kind=wechat_kind,
        opportunities=opportunities or [],
        is_opportunity=scope == "opportunity",
        site_url=SITE_URL,
        opportunity_dates=sorted((p.stem for p in (OUTPUT_DIR / "opportunities").glob("????-??-??.json")), reverse=True)[:14] if scope == "opportunity" else [],
    )


def render_opportunity_html(opportunities: list, date_str: str | None = None) -> str:
    """Render opportunities inside the same magazine shell as every news scope."""
    from services.opportunity.runtime import local_now
    display = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y年%m月%d日") if date_str else local_now().strftime("%Y年%m月%d日")
    return render_html([], output_type="web", scope="opportunity", opportunities=opportunities, date_display=display)


def render_page(articles: list, output_type: str = "web") -> Path:
    """
    渲染资讯页面并保存到output目录

    Args:
        articles: Article对象列表
        output_type: "web" 或 "wechat"

    Returns:
        生成的HTML文件路径
    """
    _, date_file = _get_date_str()
    html = render_html(articles, output_type=output_type, scope="national")

    # 保存文件：Web 版只有 {date}.html，主题由前端 CSS 切换
    suffix = "" if output_type == "web" else "_wechat"
    output_path = OUTPUT_DIR / f"{date_file}{suffix}.html"
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"页面已生成: {output_path}")

    return output_path


def render_both(articles: list) -> tuple[Path, Path]:
    """同时生成Web版本和微信公众号版本"""
    web_path = render_page(articles, "web")
    wechat_path = render_page(articles, "wechat")
    return web_path, wechat_path


def download_article_images(articles: list) -> None:
    """下载文章图片到本地，解决外部 CDN 防盗链问题

    搜狗 CDN 和微信图片域名均做 Referer 校验，
    直接在页面中引用会 403，因此下载到 output/images/ 本地化。
    """
    if not DOWNLOAD_IMAGES:
        return

    img_dir = OUTPUT_DIR / "images"
    img_dir.mkdir(exist_ok=True)

    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for article in articles:
            if not article.image_url:
                continue
            original_url = article.image_url
            try:
                referer = _get_referer_for_url(article.image_url)
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                if referer:
                    headers["Referer"] = referer

                resp = client.get(article.image_url, headers=headers)
                if resp.status_code != 200:
                    logger.debug(f"图片下载失败 [{resp.status_code}]: {article.image_url[:60]}")
                    article.image_url = original_url
                    continue

                # 微信防盗链图通常非常小（小于 5KB），其它来源可能会有合法的极小缩略图（如知乎头像）
                content = resp.content
                content_type = resp.headers.get("content-type", "")
                is_svg = "svg" in content_type.lower()
                is_wechat_img = "qpic.cn" in article.image_url or "qlogo.cn" in article.image_url or "sogoucdn.com" in article.image_url
                
                if is_wechat_img and len(content) < 5000 and not is_svg:
                    logger.debug(
                        f"图片疑似防盗链占位图（{len(content)} bytes），跳过: "
                        f"{article.image_url[:60]}"
                    )
                    article.image_url = ""
                    continue

                ext = _ext_from_content_type(content_type)
                filename = hashlib.md5(article.image_url.encode()).hexdigest()[:10] + ext
                (img_dir / filename).write_bytes(content)
                # 使用绝对路径，避免在 /archive 等路由下相对路径解析错误
                article.image_url = f"/images/{filename}"

            except Exception as e:
                logger.debug(f"图片下载异常: {e}")
                # 异常时回退到原图地址，避免页面无图
                article.image_url = original_url


def _get_referer_for_url(url: str) -> str:
    """根据图片 URL 域名返回对应的 Referer，绕过防盗链"""
    if "sogoucdn.com" in url:
        return "https://weixin.sogou.com/"
    if "mmbiz.qpic.cn" in url or "mmbiz.qlogo.cn" in url:
        return "https://mp.weixin.qq.com/"
    if "zhimg.com" in url:
        return "https://www.zhihu.com/"
    if "hdslb.com" in url or "bilibili.com" in url:
        return "https://www.bilibili.com/"
    return ""


def _ext_from_content_type(content_type: str) -> str:
    """根据 Content-Type 推断文件扩展名"""
    ct = content_type.lower()
    if "icon" in ct or "x-icon" in ct:
        return ".ico"
    if "png" in ct:
        return ".png"
    if "gif" in ct:
        return ".gif"
    if "webp" in ct:
        return ".webp"
    return ".jpg"


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
            "fetched_at": getattr(a, "fetched_at", 0.0),
            "content_quality": a.content_quality,
            "skill_name": a.skill_name,
            "source_domain": getattr(a, "source_domain", ""),
            "source_group": getattr(a, "source_group", ""),
            "source_kind": getattr(a, "source_kind", ""),
            "collection_url": getattr(a, "collection_url", ""),
            "news_category": getattr(a, "news_category", ""),
            "relevance_score": getattr(a, "relevance_score", 0.0),
            "region_scope": getattr(a, "region_scope", "national"),
            "demand_signal_score": getattr(a, "demand_signal_score", 0.0),
            "is_potential_warehouse_demand": getattr(a, "is_potential_warehouse_demand", False),
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
