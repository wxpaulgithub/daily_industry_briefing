"""
智能仓储每日简讯 - FastAPI Web 服务
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import HOST, PORT, OUTPUT_DIR, STATIC_DIR, SCHEDULE_HOUR, SCHEDULE_MINUTE, DEFAULT_SCOPE
from services.fetcher import fetch_all_news, fetch_news_by_scope, Article
from services.generator import render_both, save_articles_json, load_articles_json, render_html
from services.wechat_sources import source_match_scope

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 全局状态
_is_fetching = False
_current_fetch_scope = "all"


def _normalize_wechat_kind(v: str | None) -> str:
    x = (v or "").strip().lower()
    if x in ("industry", "local"):
        return x
    return "all"


def _article_in_scope(article: Article, scope: str) -> bool:
    s = (article.region_scope or "national").strip().lower()
    wk = (article.wechat_category or "industry").strip().lower()
    is_local_source = source_match_scope(article.source_name, "local")
    if scope == "local":
        return (s == "local") or (s == "wechat" and (wk == "local" or is_local_source))
    if scope == "discover":
        return s == "discover"
    if scope == "wechat":
        return s == "wechat" and wk != "local" and (not is_local_source)
    return s not in ("local", "discover", "wechat")


def _merge_scope_articles(existing: list[Article], scoped: list[Article], scope: str) -> list[Article]:
    """用局部抓取结果替换对应 scope，其余 scope 保持不变"""
    kept = [a for a in existing if not _article_in_scope(a, scope)]
    merged = kept + scoped
    merged.sort(key=lambda a: a.published_ts, reverse=True)
    return merged


async def _do_fetch(scope: str | None = None):
    """执行采集+生成流程；scope 为空表示全量刷新"""
    global _is_fetching, _current_fetch_scope
    if _is_fetching:
        logger.info("采集已在进行中，跳过")
        return

    normalized_scope = "all"
    if scope is not None:
        normalized_scope = _normalize_scope(scope)

    _is_fetching = True
    _current_fetch_scope = normalized_scope
    try:
        if normalized_scope == "all":
            logger.info("开始全量采集资讯...")
            articles = await fetch_all_news()
        else:
            logger.info(f"开始局部采集资讯... scope={normalized_scope}")
            scoped_articles = await fetch_news_by_scope(normalized_scope)
            date_str = datetime.now().strftime("%Y-%m-%d")
            existing_data = load_articles_json(date_str)
            existing_articles = [Article(**d) for d in existing_data] if existing_data else []
            articles = _merge_scope_articles(existing_articles, scoped_articles, normalized_scope)

        if not articles:
            logger.warning("未采集到任何资讯")
            return

        # 生成 Web 版和微信公众号版
        render_both(articles)
        save_articles_json(articles)
        logger.info(f"资讯生成完成: {len(articles)} 条（scope={normalized_scope}）")
    except Exception as e:
        logger.error(f"采集失败: {e}", exc_info=True)
    finally:
        _is_fetching = False
        _current_fetch_scope = "all"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动定时任务"""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _do_fetch,
        CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id="daily_fetch",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"定时任务已启动: 每天 {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")

    yield

    scheduler.shutdown(wait=False)


app = FastAPI(title="智能仓储每日简讯", lifespan=lifespan)

# 静态文件
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 资讯图片目录（output/images）用于页面中的 images/... 路径
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR = OUTPUT_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


# ===== 页面路由 =====

@app.get("/", response_class=HTMLResponse)
async def index(scope: str = Query(DEFAULT_SCOPE), wechat_kind: str = Query("all")):
    """今日资讯页面，主题由前端 localStorage + CSS 切换"""
    scope = _normalize_scope(scope)
    wechat_kind = _normalize_wechat_kind(wechat_kind)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # 优先从 JSON 动态渲染，支持 scope 切换
    data = load_articles_json(date_str)
    if data:
        articles = [Article(**d) for d in data]
        filtered = _filter_articles_by_scope(articles, scope, wechat_kind)
        return HTMLResponse(content=render_html(filtered, output_type="web", scope=scope, wechat_kind=wechat_kind))

    # 兜底：旧文件兼容（仅 national）
    html_file = OUTPUT_DIR / f"{date_str}.html"
    if scope == "national" and html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))

    # 没有今日数据，返回加载页面并触发后台采集
    asyncio.create_task(_do_fetch(scope))
    return HTMLResponse(content=_loading_page(scope, wechat_kind))


@app.get("/wechat", response_class=HTMLResponse)
async def wechat_page():
    """微信公众号版本"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    wechat_file = OUTPUT_DIR / f"{date_str}_wechat.html"

    if wechat_file.exists():
        return HTMLResponse(content=wechat_file.read_text(encoding="utf-8"))

    raise HTTPException(status_code=404, detail="今日微信公众号版尚未生成，请先刷新资讯")


@app.get("/archive/{date_str}", response_class=HTMLResponse)
async def archive(date_str: str, scope: str = Query(DEFAULT_SCOPE), wechat_kind: str = Query("all")):
    """历史资讯页面"""
    scope = _normalize_scope(scope)
    wechat_kind = _normalize_wechat_kind(wechat_kind)
    data = load_articles_json(date_str)
    if data:
        articles = [Article(**d) for d in data]
        filtered = _filter_articles_by_scope(articles, scope, wechat_kind)
        html = render_html(filtered, output_type="web", scope=scope, wechat_kind=wechat_kind)
        return HTMLResponse(content=html)

    archive_file = OUTPUT_DIR / f"{date_str}.html"
    if scope == "national" and archive_file.exists():
        return HTMLResponse(content=archive_file.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail=f"未找到 {date_str} 的资讯")


# ===== API 路由 =====

@app.api_route("/api/refresh", methods=["GET", "POST"])
async def refresh(scope: str = Query(DEFAULT_SCOPE)):
    """手动触发重新采集（支持 GET 和 POST），按 scope 局部刷新"""
    global _is_fetching
    if _is_fetching:
        return JSONResponse({"status": "already_fetching", "scope": _current_fetch_scope})

    normalized_scope = _normalize_scope(scope)
    asyncio.create_task(_do_fetch(normalized_scope))
    return JSONResponse({"status": "started", "scope": normalized_scope})


@app.get("/api/news/today")
async def news_today(scope: str = Query(DEFAULT_SCOPE), wechat_kind: str = Query("all")):
    """今日资讯JSON数据"""
    scope = _normalize_scope(scope)
    wechat_kind = _normalize_wechat_kind(wechat_kind)
    date_str = datetime.now().strftime("%Y-%m-%d")
    data = load_articles_json(date_str)
    if not data:
        return JSONResponse({"articles": [], "date": date_str, "scope": scope, "wechat_kind": wechat_kind})
    articles = _filter_articles_by_scope([Article(**d) for d in data], scope, wechat_kind)
    return JSONResponse({"articles": [a.__dict__ for a in articles], "date": date_str, "scope": scope, "wechat_kind": wechat_kind})


@app.get("/api/news/{date_str}")
async def news_by_date(date_str: str, scope: str = Query(DEFAULT_SCOPE), wechat_kind: str = Query("all")):
    """指定日期资讯JSON数据"""
    scope = _normalize_scope(scope)
    wechat_kind = _normalize_wechat_kind(wechat_kind)
    data = load_articles_json(date_str)
    if not data:
        raise HTTPException(status_code=404, detail=f"未找到 {date_str} 的资讯数据")
    articles = _filter_articles_by_scope([Article(**d) for d in data], scope, wechat_kind)
    return JSONResponse({"articles": [a.__dict__ for a in articles], "date": date_str, "scope": scope, "wechat_kind": wechat_kind})


@app.get("/api/status")
async def status():
    """服务状态"""
    return JSONResponse({
        "fetching": _is_fetching,
        "fetch_scope": _current_fetch_scope,
        "schedule": f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}",
        "available_dates": _get_available_dates(),
    })


# ===== 工具函数 =====

def _get_available_dates() -> list[str]:
    """获取已有资讯的日期列表"""
    dates = []
    for f in sorted(OUTPUT_DIR.glob("*.html"), reverse=True):
        name = f.stem.replace("_wechat", "")
        # 跳过带主题后缀的旧文件名
        if "_" in name:
            continue
        if name not in dates:
            dates.append(name)
    return dates[:30]  # 最多保留30天


def _normalize_scope(scope: str) -> str:
    v = (scope or "").strip().lower()
    if v == "local":
        return "local"
    if v == "discover":
        return "discover"
    if v == "wechat":
        return "wechat"
    return "national"


def _filter_articles_by_scope(articles: list[Article], scope: str, wechat_kind: str = "all") -> list[Article]:
    if scope == "local":
        return [
            a for a in articles
            if ((a.region_scope or "national") == "local")
            or (
                (a.region_scope or "national") == "wechat"
                and (
                    ((a.wechat_category or "industry").strip().lower() == "local")
                    or source_match_scope(a.source_name, "local")
                )
            )
        ]
    if scope == "discover":
        return [a for a in articles if (a.region_scope or "national") == "discover"]
    if scope == "wechat":
        return [
            a for a in articles
            if (a.region_scope or "national") == "wechat"
            and ((a.wechat_category or "industry").strip().lower() != "local")
            and (not source_match_scope(a.source_name, "local"))
        ]
    return [
        a for a in articles
        if (a.region_scope or "national") not in ("local", "discover", "wechat")
    ]


def _loading_page(scope: str = "national", wechat_kind: str = "all") -> str:
    """返回加载中页面"""
    date_display = datetime.now().strftime("%Y年%m月%d日")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>智能仓储每日简讯 · {date_display}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #FAFAFA; min-height: 100vh; display: flex; align-items: center; justify-content: center; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }}
.loading {{ text-align: center; padding: 24px; }}
.spinner {{ width: 28px; height: 28px; border: 2px solid #E0E0E0; border-top-color: #999; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.text {{ margin-top: 14px; font-size: 13px; color: #BBB; letter-spacing: 1px; }}
.brand-logo {{ width: 96px; height: auto; display: block; margin: 0 auto 108px; opacity: 0.9; }}
.brand {{ font-family: "Noto Serif SC", Georgia, serif; font-size: 20px; color: #1A1A1A; letter-spacing: 2px; margin-bottom: 24px; }}
</style>
</head>
<body>
<div class="loading">
  <img class="brand-logo" src="/static/logo.png" alt="Mhstar">
  <div class="brand">智能仓储每日简讯</div>
  <div class="spinner"></div>
  <div class="text">正在采集今日资讯，请稍候...</div>
</div>
<script>
// 轮询检查是否生成完成
let attempts = 0;
const check = setInterval(async () => {{
  attempts++;
  try {{
    const res = await fetch('/api/news/today?scope={scope}&wechat_kind={wechat_kind}');
    const data = await res.json();
    if (data.articles && data.articles.length > 0) {{
      clearInterval(check);
      location.reload();
    }}
  }} catch(e) {{}}
  if (attempts > 60) {{  // 最多等2分钟
    clearInterval(check);
    document.querySelector('.text').textContent = '采集超时，请刷新页面重试';
  }}
}}, 2000);
</script>
</body>
</html>"""


# ===== 启动入口 =====

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
