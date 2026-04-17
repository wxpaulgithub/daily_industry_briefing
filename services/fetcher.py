"""
资讯采集服务 - 可插拔 Skill 架构

每个数据源实现为一个 NewsSkill，通过 SKILLS 列表注册。
新增数据源只需：
  1. 在 skills/ 目录下创建新文件，继承 NewsSkill
  2. 在 skills/__init__.py 的 SKILLS 列表中注册
"""
import asyncio
import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from html import unescape

import httpx

from config import (
    MAX_ARTICLES,
    REQUEST_TIMEOUT,
    SUMMARY_MAX_LENGTH,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


# ===== 数据模型 =====

@dataclass
class Article:
    """单条资讯文章"""
    title: str
    url: str
    summary: str = ""
    source_name: str = ""
    image_url: str = ""
    published: str = ""
    published_ts: float = 0.0
    fetched_at: float = field(default_factory=time.time)
    content_quality: float = 0.0

    @property
    def uid(self) -> str:
        return hashlib.md5(self.url.encode()).hexdigest()[:12]


# ===== Skill 基类 =====

class NewsSkill(ABC):
    """数据源 Skill 基类，所有数据源继承此类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Skill 名称，用于日志标识"""
        ...

    @property
    @abstractmethod
    def search_queries(self) -> list[dict]:
        """搜索关键词配置列表，每项 {"keyword": "...", "label": "..."} """
        ...

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient, keyword: str, count: int = 10) -> list[Article]:
        """从数据源获取资讯，子类必须实现"""
        ...

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        """并发搜索所有关键词并汇总结果（通用实现，子类一般无需重写）"""
        tasks = [self.fetch(client, q["keyword"], count=10) for q in self.search_queries]
        results = await asyncio.gather(*tasks)
        articles = []
        for batch in results:
            articles.extend(batch)
        logger.info(f"[{self.name}] 采集完成，获取 {len(articles)} 条原始资讯")
        return articles


# ===== 公共工具函数 =====

def normalize_summary(text: str, max_len: int = SUMMARY_MAX_LENGTH) -> str:
    """清理并截断摘要"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"&\w+;", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        truncated = text[:max_len].rsplit("。", 1)[0]
        if len(truncated) < max_len // 2:
            text = text[:max_len] + "..."
        else:
            text = truncated + "..."
    return text


def clean_title(title: str) -> str:
    """清理标题中的HTML实体和特殊字符"""
    title = unescape(title)
    title = re.sub(r"&\w+;", "", title)
    return title.strip()


# ===== 内容过滤规则 =====

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

EXCLUDE_KEYWORDS = [
    "村支书", "村干部", "纪委", "反腐", "落马", "违纪",
    "贪腐", "受贿", "举报", "巡视", "处分",
    "房价", "楼市", "股票", "A股", "涨停", "跌停",
    "明星", "综艺", "娱乐", "电影", "电视剧",
    "高考", "招生", "学区", "中考",
    "疫情", "核酸", "疫苗",
    "贪污", "官员", "书记", "局长",
]


def filter_relevant(articles: list[Article]) -> list[Article]:
    """过滤掉与工业领域无关的文章"""
    relevant = []
    for article in articles:
        title = article.title.lower()
        if any(kw in title for kw in EXCLUDE_KEYWORDS):
            continue
        text = (article.title + article.summary).lower()
        if any(kw.lower() in text for kw in REQUIRED_KEYWORDS):
            relevant.append(article)
    return relevant


def deduplicate(articles: list[Article]) -> list[Article]:
    """按标题相似度去重"""
    seen_titles: list[str] = []
    unique: list[Article] = []
    strip_re = "[，。、！？：；\u201c\u201d\u2018\u2019（）【】\\s]+"

    for article in articles:
        title_clean = re.sub(strip_re, "", article.title.lower().strip())
        is_dup = False
        for seen in seen_titles:
            seen_clean = re.sub(strip_re, "", seen)
            if not title_clean or not seen_clean:
                continue
            common = sum(1 for c in title_clean if c in seen_clean)
            ratio = common / max(len(title_clean), len(seen_clean), 1)
            if ratio > 0.6:
                is_dup = True
                break
        if not is_dup:
            seen_titles.append(article.title.lower().strip())
            unique.append(article)
    return unique


def score_article(article: Article) -> float:
    """为文章打分"""
    score = 0.4
    if article.published_ts > 0:
        hours_ago = (time.time() - article.published_ts) / 3600
        if hours_ago < 24:
            score += 0.25
        elif hours_ago < 72:
            score += 0.18
        elif hours_ago < 168:
            score += 0.12
        elif hours_ago < 720:
            score += 0.06
    if article.image_url:
        score += 0.12
    if len(article.summary) > 80:
        score += 0.08
    elif len(article.summary) > 30:
        score += 0.04
    if 10 < len(article.title) < 60:
        score += 0.05
    trusted = [
        "新华社", "人民日报", "央视", "新华网", "人民网",
        "每日经济新闻", "第一财经", "经济日报", "21世纪经济报道",
        "澎湃新闻", "界面新闻", "中国工业报", "中国工控网", "36氪",
    ]
    for src in trusted:
        if src in article.source_name:
            score += 0.1
            break
    return min(score, 1.0)


def select_articles(articles: list[Article], count: int) -> list[Article]:
    """精选最终列表"""
    scored = [(a, score_article(a)) for a in articles]
    scored.sort(key=lambda x: x[1], reverse=True)

    selected: list[Article] = []
    selected_set: set[str] = set()
    remaining = list(scored)

    while len(selected) < count and remaining:
        for i, (art, _) in enumerate(remaining):
            uid = art.uid
            if uid in selected_set:
                continue
            if art.published_ts > 0:
                age_days = (time.time() - art.published_ts) / 86400
                if age_days > 30:
                    continue
            selected.append(art)
            selected_set.add(uid)
            remaining.pop(i)
            break
        else:
            for i, (art, _) in enumerate(remaining):
                if art.uid not in selected_set:
                    selected.append(art)
                    selected_set.add(art.uid)
                    remaining.pop(i)
                    break
            else:
                break

    selected.sort(key=lambda a: a.published_ts, reverse=True)
    return selected[:count]


# ===== 主入口 =====

async def fetch_all_news() -> list[Article]:
    """采集所有已注册 Skill 的资讯，合并去重后精选"""
    from services.skills import SKILLS  # 延迟导入，避免循环依赖

    all_articles: list[Article] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        # 并发运行所有 Skill
        tasks = [skill.fetch_all(client) for skill in SKILLS]
        results = await asyncio.gather(*tasks)
        for articles in results:
            all_articles.extend(articles)

    logger.info(f"所有 Skill 采集完成，共 {len(all_articles)} 条")

    all_articles = filter_relevant(all_articles)
    logger.info(f"相关性过滤后 {len(all_articles)} 条")

    all_articles = deduplicate(all_articles)
    logger.info(f"去重后 {len(all_articles)} 条")

    selected = select_articles(list(all_articles), MAX_ARTICLES)
    logger.info(f"最终精选 {len(selected)} 条")
    return selected
