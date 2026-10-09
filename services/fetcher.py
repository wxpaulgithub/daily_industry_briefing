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
    DEFAULT_SCOPE,
    LOCAL_EXCLUDE_KEYWORDS,
    LOCAL_INDUSTRY_KEYWORDS,
    LOCAL_INTENT_KEYWORDS,
    LOCAL_REGION_KEYWORDS,
    MAX_ARTICLES,
    MAX_ARTICLES_DISCOVER,
    MAX_ARTICLES_LOCAL,
    MAX_ARTICLES_WECHAT,
    MAX_ARTICLE_AGE_DAYS,
    NATIONAL_SOURCE_MAX_COUNT,
    NATIONAL_UNKNOWN_DATE_MAX_COUNT,
    RUNTIME_DIR,
    REQUEST_TIMEOUT,
    SKILL_CACHE_TTL_SECONDS,
    SKILL_FAILURE_COOLDOWN_SECONDS,
    SKILL_FAILURE_THRESHOLD,
    SKILL_FETCH_TIMEOUT_SECONDS,
    SUMMARY_MAX_LENGTH,
    USER_AGENT,
)
from services.wechat_sources import source_match_scope

from services.national_news import (is_national, annotate, assess, deduplicate_national, quality_score, select_national_articles, write_diagnostics)

logger = logging.getLogger(__name__)


@dataclass
class SkillRuntimeState:
    """单个 Skill 的运行态（缓存、失败计数、冷却）"""
    cache_articles: list["Article"] = field(default_factory=list)
    cache_at: float = 0.0
    failure_count: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    last_status: str = "init"
    last_duration: float = 0.0


_SKILL_RUNTIME: dict[str, SkillRuntimeState] = {}


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
    skill_name: str = ""  # 来源 Skill 标识，用于差异化过滤
    region_scope: str = DEFAULT_SCOPE  # national / local / discover / wechat
    wechat_category: str = ""  # wechat 页子分类：industry / local
    demand_signal_score: float = 0.0
    is_potential_warehouse_demand: bool = False
    source_domain: str = ""
    source_group: str = ""
    source_kind: str = ""
    collection_url: str = ""
    news_category: str = ""
    relevance_score: float = 0.0

    @property
    def uid(self) -> str:
        return hashlib.md5(self.url.encode()).hexdigest()[:12]


def _clone_articles(articles: list["Article"]) -> list["Article"]:
    """缓存/降级回放时复制对象，避免后续流程污染原缓存"""
    return [Article(**a.__dict__) for a in articles]


def _is_cache_fresh(state: SkillRuntimeState, now_ts: float) -> bool:
    return bool(state.cache_articles) and (now_ts - state.cache_at) <= SKILL_CACHE_TTL_SECONDS


async def _run_skill_with_guard(skill: "NewsSkill", client: httpx.AsyncClient) -> tuple[str, list["Article"], str, float]:
    """执行 Skill，并提供超时、缓存命中、失败降级与冷却保护"""
    skill_name = (getattr(skill, "name", "") or skill.__class__.__name__).strip() or skill.__class__.__name__
    state = _SKILL_RUNTIME.setdefault(skill_name, SkillRuntimeState())
    now_ts = time.time()

    # 冷却窗口：优先用缓存兜底
    if state.cooldown_until > now_ts:
        remain = max(int(state.cooldown_until - now_ts), 0)
        if _is_cache_fresh(state, now_ts):
            logger.warning(f"[{skill_name}] 处于冷却期（剩余 {remain}s），使用缓存结果")
            state.last_status = "cooldown_cache"
            return skill_name, _clone_articles(state.cache_articles), "cooldown_cache", 0.0
        logger.warning(f"[{skill_name}] 处于冷却期（剩余 {remain}s），且无可用缓存，跳过本轮")
        state.last_status = "cooldown_skip"
        return skill_name, [], "cooldown_skip", 0.0

    # 新鲜缓存直接返回，减少重复抓取
    if _is_cache_fresh(state, now_ts):
        age = int(now_ts - state.cache_at)
        logger.info(f"[{skill_name}] 命中缓存（{age}s 前），跳过远程抓取")
        state.last_status = "cache"
        return skill_name, _clone_articles(state.cache_articles), "cache", 0.0

    start = time.time()
    try:
        articles = await asyncio.wait_for(skill.fetch_all(client), timeout=SKILL_FETCH_TIMEOUT_SECONDS)
        duration = time.time() - start
        state.failure_count = 0
        state.cooldown_until = 0.0
        state.last_error = ""
        state.last_status = "live"
        state.last_duration = duration
        state.cache_articles = _clone_articles(articles)
        state.cache_at = time.time()
        logger.info(f"[{skill_name}] 实时抓取完成，{len(articles)} 条，耗时 {duration:.2f}s")
        return skill_name, articles, "live", duration
    except asyncio.TimeoutError:
        duration = time.time() - start
        state.failure_count += 1
        state.last_error = f"Timeout>{SKILL_FETCH_TIMEOUT_SECONDS}s"
        state.last_status = "timeout"
        state.last_duration = duration
    except Exception as e:
        duration = time.time() - start
        state.failure_count += 1
        state.last_error = repr(e)
        state.last_status = "error"
        state.last_duration = duration

    # 失败后达到阈值，进入冷却
    if state.failure_count >= SKILL_FAILURE_THRESHOLD:
        state.cooldown_until = time.time() + SKILL_FAILURE_COOLDOWN_SECONDS
        logger.warning(
            f"[{skill_name}] 连续失败 {state.failure_count} 次，进入冷却 {SKILL_FAILURE_COOLDOWN_SECONDS}s"
        )

    # 失败兜底：尽量使用缓存
    fallback_now = time.time()
    if _is_cache_fresh(state, fallback_now):
        logger.warning(f"[{skill_name}] 抓取失败，降级使用缓存: {state.last_error}")
        state.last_status = "degraded_cache"
        return skill_name, _clone_articles(state.cache_articles), "degraded_cache", state.last_duration

    logger.warning(f"[{skill_name}] 抓取失败且无缓存可用: {state.last_error}")
    return skill_name, [], "failed", state.last_duration


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

    @property
    def region_scope(self) -> str:
        """数据范围：local 或 national"""
        return "national"

    async def fetch_all(self, client: httpx.AsyncClient) -> list[Article]:
        """并发搜索所有关键词并汇总结果（通用实现，子类一般无需重写）"""
        self.source_diagnostics = []
        tasks = [self.fetch(client, q["keyword"], count=10) for q in self.search_queries]
        results = await asyncio.gather(*tasks)
        articles = []
        for batch in results:
            for a in batch:
                a.skill_name = self.name
                if not a.region_scope:
                    a.region_scope = self.region_scope
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
    "中鼎", "承亿", "昆船", "北自院", "立库集成", "兰剑",
]

EXCLUDE_KEYWORDS = [
    "村支书", "村干部", "纪委", "反腐", "落马", "违纪",
    "贪腐", "受贿", "举报", "巡视", "处分",
    "房价", "楼市", "股票", "A股", "涨停", "跌停",
    "明星", "综艺", "娱乐", "电影", "电视剧",
    "高考", "招生", "学区", "中考",
    "疫情", "核酸", "疫苗", "以旧换新",
    "人形机器人", "具身机器人", "机器狗", "四足机器人",
    "服务机器人", "养老机器人", "陪伴机器人",
    "贪污", "官员", "书记", "局长",
]

SKILLS_USE_PRE_FILTER = {
    "本地项目",
    "微信公众号",
    "本地公众号",
    "公众号RSS",
    "政策标准",
    "招投标",
    "行业媒体",
    "展会协会",
    "知乎发现",
    "B站发现",
}

VENDOR_PRIORITY_KEYWORDS = [
    # 核心厂商关键词（智能仓储/物流自动化）
    "中鼎",
    "中鼎集成",
    "承亿",
    "昆船",
    "昆船智能",
    "北自院",
    "北自所",
    "兰剑",
    "今天国际",
    "北自科技",
    "井松智能",
    "音飞储存",
    "德马科技",
    "诺力股份",
    "中科微至",
    "东杰智能",
    "机器人股份",
    "新松",
    "海康机器人",
    "极智嘉",
    "海柔创新",
    "快仓",
    "劢微机器人",
    "库卡",
    "ABB",
    "西门子",
    "施耐德",
    "德马泰克",
    "Dematic",
    "瑞仕格",
    "Swisslog",
    "大福",
    "Daifuku",
    "胜斐迩",
    "SSI Schaefer",
    "KNAPP",
    "范德兰德",
    "Vanderlande",
    "TGW",
]

ROBOT_CORE_KEYWORDS = [
    "机器人", "人形机器人", "具身智能", "机械臂", "协作机器人",
    "服务机器人", "陪伴机器人", "医疗机器人", "四足机器人",
]

WAREHOUSE_CONTEXT_KEYWORDS = [
    "仓储", "仓库", "立库", "堆垛机", "物流", "供应链",
    "wms", "wcs", "mes", "agv", "amr", "输送线", "拣选", "分拣",
    "工厂", "产线", "制造", "工业",
]

DEMAND_SIGNAL_KEYWORDS_STRONG = [
    "引进项目", "最新项目", "重大项目", "招商引资", "产业园",
    "新建厂房", "扩建", "开工", "投产", "落地", "签约",
    "物流中心", "智能仓储", "自动化立库",
]

DEMAND_SIGNAL_KEYWORDS_MEDIUM = [
    "技改", "数字化改造", "产线升级", "设备更新", "扩产",
]

BIDDING_PRIORITY_KEYWORDS = [
    "招标", "中标", "中标候选人", "成交公告", "采购公告", "招标公告",
    "公开招标", "竞争性磋商", "竞争性谈判", "单一来源", "EPC", "总包",
    "立体库", "堆垛机", "输送线", "WMS", "WCS", "AGV", "物流自动化",
]


def _is_pure_robot_topic(article: Article) -> bool:
    """识别“机器人相关但缺少智能仓储/工业上下文”的文章"""
    text = (article.title + " " + article.summary).lower()
    has_robot = any(kw.lower() in text for kw in ROBOT_CORE_KEYWORDS)
    has_warehouse_context = any(kw.lower() in text for kw in WAREHOUSE_CONTEXT_KEYWORDS)
    return has_robot and not has_warehouse_context


def _has_vendor_priority_hit(article: Article) -> bool:
    """命中厂商关键词则视为优先内容"""
    text = (article.title + " " + article.summary + " " + article.source_name).lower()
    return any(kw.lower() in text for kw in VENDOR_PRIORITY_KEYWORDS)


def _has_bidding_priority_hit(article: Article) -> bool:
    """命中招投标关键词则优先"""
    text = (article.title + " " + article.summary + " " + article.source_name).lower()
    return any(kw.lower() in text for kw in BIDDING_PRIORITY_KEYWORDS)


def _is_local_region_hit(article: Article) -> bool:
    """本地文章必须命中区域词，避免本地/国内页面内容同质化"""
    text = (article.title + " " + article.summary + " " + article.source_name).lower()
    return any(kw.lower() in text for kw in LOCAL_REGION_KEYWORDS)


def _is_local_intent_hit(article: Article) -> bool:
    """本地资讯需命中项目/投资意图关键词"""
    text = (article.title + " " + article.summary).lower()
    return any(kw.lower() in text for kw in LOCAL_INTENT_KEYWORDS)


def _is_local_industry_hit(article: Article) -> bool:
    """本地资讯需命中工业/物流场景关键词"""
    text = (article.title + " " + article.summary).lower()
    return any(kw.lower() in text for kw in LOCAL_INDUSTRY_KEYWORDS)


def _is_local_excluded(article: Article) -> bool:
    """本地资讯硬排除关键词"""
    text = (article.title + " " + article.summary + " " + article.source_name).lower()
    return any(kw.lower() in text for kw in LOCAL_EXCLUDE_KEYWORDS)


def _is_local_whitelist_source(article: Article) -> bool:
    """来源是否命中本地公众号白名单"""
    return source_match_scope(article.source_name, "local")


def _passes_local_strict(article: Article) -> bool:
    """本地内容严格准入：区域 + 项目意图 + 至少两个工业场景词"""
    text = (article.title + " " + article.summary).lower()
    intent_hits = _count_hits(text, LOCAL_INTENT_KEYWORDS)
    industry_hits = _count_hits(text, LOCAL_INDUSTRY_KEYWORDS)
    return intent_hits >= 1 and industry_hits >= 2


def _passes_local_fallback(article: Article) -> bool:
    """
    本地内容温和兜底：
    仅对白名单来源放宽，避免最后一条都不剩。
    规则仍要求：
    1. 已命中区域词
    2. 已通过硬排除
    3. 必须同时具备项目意图和工业场景各至少 1 个
    """
    if not _is_local_whitelist_source(article):
        return False
    text = (article.title + " " + article.summary).lower()
    intent_hits = _count_hits(text, LOCAL_INTENT_KEYWORDS)
    industry_hits = _count_hits(text, LOCAL_INDUSTRY_KEYWORDS)
    return intent_hits >= 1 and industry_hits >= 1


def _passes_local_prefiltered(article: Article) -> bool:
    """
    对“本地项目”和本地白名单公众号做温和准入。
    这些来源本身已经是定向检索或定向订阅，不需要再套过严的双重门槛。
    但仍要求至少有“项目意图”或“工业场景”其一命中，避免放进纯民生信息。
    """
    text = (article.title + " " + article.summary).lower()
    intent_hits = _count_hits(text, LOCAL_INTENT_KEYWORDS)
    industry_hits = _count_hits(text, LOCAL_INDUSTRY_KEYWORDS)

    if article.skill_name == "本地项目":
        return intent_hits >= 1 or industry_hits >= 1

    if _is_local_whitelist_source(article):
        return (
            intent_hits >= 1
            or industry_hits >= 2
            or (_has_vendor_priority_hit(article) and industry_hits >= 1)
        )

    return False


def _is_local_fallback_candidate(article: Article) -> bool:
    """
    本地页最终为空时的兜底候选。
    这里允许比严格精选稍微放宽，但仍然要求：
    1. 已经属于本地池
    2. 通过本地硬排除
    3. 命中区域词
    4. 至少 1 个项目意图词
    5. 至少 1 个工业场景词
    """
    if _is_local_excluded(article):
        return False
    if not _is_local_region_hit(article):
        return False
    text = (article.title + " " + article.summary).lower()
    intent_hits = _count_hits(text, LOCAL_INTENT_KEYWORDS)
    industry_hits = _count_hits(text, LOCAL_INDUSTRY_KEYWORDS)
    return intent_hits >= 1 and industry_hits >= 1


def _count_hits(text: str, keywords: list[str]) -> int:
    t = (text or "").lower()
    return sum(1 for kw in keywords if kw and kw.lower() in t)


def _calc_demand_signal_score(article: Article) -> float:
    """计算潜在仓储需求信号分"""
    text = (article.title + " " + article.summary).lower()
    score = 0.0
    if any(kw.lower() in text for kw in DEMAND_SIGNAL_KEYWORDS_STRONG):
        score += 0.7
    if any(kw.lower() in text for kw in DEMAND_SIGNAL_KEYWORDS_MEDIUM):
        score += 0.3
    if any(kw.lower() in text for kw in WAREHOUSE_CONTEXT_KEYWORDS):
        score += 0.3
    if any(kw.lower() in text for kw in LOCAL_REGION_KEYWORDS):
        score += 0.2
    return min(score, 1.2)


def _article_diag_summary(articles: list[Article]) -> str:
    """生成统一的文章诊断摘要，便于定位分流/标签问题"""
    if not articles:
        return "total=0"

    region_counts: dict[str, int] = {}
    skill_counts: dict[str, int] = {}
    wechat_counts: dict[str, int] = {}

    for a in articles:
        region = (a.region_scope or "national").strip().lower() or "national"
        skill = (a.skill_name or "unknown").strip() or "unknown"
        wk = (a.wechat_category or "").strip().lower()

        region_counts[region] = region_counts.get(region, 0) + 1
        skill_counts[skill] = skill_counts.get(skill, 0) + 1
        if wk:
            wechat_counts[wk] = wechat_counts.get(wk, 0) + 1

    region_text = ", ".join(f"{k}:{v}" for k, v in sorted(region_counts.items()))
    skill_text = ", ".join(
        f"{k}:{v}" for k, v in sorted(skill_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
    )
    wechat_text = ", ".join(f"{k}:{v}" for k, v in sorted(wechat_counts.items()))

    parts = [f"total={len(articles)}", f"region=[{region_text}]"]
    if wechat_text:
        parts.append(f"wechat_category=[{wechat_text}]")
    if skill_text:
        parts.append(f"skill_top=[{skill_text}]")
    return ", ".join(parts)


def filter_relevant(articles: list[Article]) -> list[Article]:
    """过滤掉与工业领域无关的文章

    公众号文章（skill_name="微信公众号"）已通过搜索关键词预筛选，
    仅做排除过滤，不做关键词命中检测，避免误杀。
    其他数据源仍执行完整的「排除 + 必须命中」双重过滤。
    """
    relevant = []
    for article in articles:
        scope = (article.region_scope or "national")
        if is_national(article):
            annotate(article)
            if not assess(article)[2]:
                relevant.append(article)
            continue
        full_text = (article.title + " " + article.summary + " " + article.source_name).lower()
        # 所有来源统一排除无关内容
        if any(kw.lower() in full_text for kw in EXCLUDE_KEYWORDS):
            continue
        # 过滤纯机器人内容（发现页放宽，保留更多探索结果）
        if scope != "discover" and _is_pure_robot_topic(article):
            continue
        # 本地范围：三条件准入 + 本地硬排除
        if scope == "local":
            if _is_local_excluded(article):
                continue
            if not _is_local_region_hit(article):
                continue
            if not (
                _passes_local_strict(article)
                or _passes_local_fallback(article)
                or _passes_local_prefiltered(article)
            ):
                continue

        if article.skill_name in SKILLS_USE_PRE_FILTER:
            # 这些来源在 Skill 内已做关键词预筛选，这里仅做排除过滤
            relevant.append(article)
        else:
            # 其他来源：必须命中行业关键词才保留
            text = (article.title + article.summary).lower()
            if any(kw.lower() in text for kw in REQUIRED_KEYWORDS):
                relevant.append(article)
    return relevant


def deduplicate(articles: list[Article]) -> list[Article]:
    """按标题相似度去重"""
    national = deduplicate_national([a for a in articles if is_national(a)])
    articles = [a for a in articles if not is_national(a)]
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
    return national + unique


def score_article(article: Article) -> float:
    """为文章打分"""
    if is_national(article):
        return quality_score(article)
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
    # 厂商相关内容优先显示
    if _has_vendor_priority_hit(article):
        score += 0.2
    # 招投标内容优先显示
    if article.skill_name == "招投标" or _has_bidding_priority_hit(article):
        score += 0.18
    # 本地潜在仓储需求项优先
    score += article.demand_signal_score * 0.2
    return min(score, 1.2)


def select_articles(articles: list[Article], count: int) -> list[Article]:
    """精选最终列表，按数据源轮选保障多样性"""
    scored = [(a, score_article(a)) for a in articles]

    # 按 skill_name 分组，组内按分数降序排列
    groups: dict[str, list[tuple]] = {}
    for item in scored:
        key = item[0].skill_name or "default"
        groups.setdefault(key, []).append(item)
    for key in groups:
        # 同分时优先厂商命中内容
        groups[key].sort(
            key=lambda x: (_has_vendor_priority_hit(x[0]), x[1]),
            reverse=True,
        )

    # 记录已选文章 uid，防止跨组重复
    selected: list[Article] = []
    selected_set: set[str] = set()

    def _try_pick(item: tuple) -> bool:
        """尝试选入一篇文章，超龄或重复则跳过"""
        art, _ = item
        if art.uid in selected_set:
            return False
        if art.published_ts > 0:
            age_days = (time.time() - art.published_ts) / 86400
            if age_days > MAX_ARTICLE_AGE_DAYS:
                return False
        selected.append(art)
        selected_set.add(art.uid)
        return True

    # 轮流从各组取分数最高的文章（类似蛇形选秀）
    group_names = list(groups.keys())
    group_idx = {name: 0 for name in group_names}  # 每组当前候选位置
    rounds_without_pick = 0
    max_stalls = len(group_names) + 1

    while len(selected) < count and rounds_without_pick < max_stalls:
        picked_this_round = False
        for name in group_names:
            if len(selected) >= count:
                break
            idx = group_idx[name]
            queue = groups[name]
            # 从当前位置向后找第一个可用的
            while idx < len(queue):
                if _try_pick(queue[idx]):
                    picked_this_round = True
                    group_idx[name] = idx + 1
                    break
                idx += 1
            else:
                group_idx[name] = len(queue)  # 该组已耗尽
        if not picked_this_round:
            rounds_without_pick += 1
        else:
            rounds_without_pick = 0

    selected.sort(key=lambda a: a.published_ts, reverse=True)
    return selected[:count]


def _is_discover_placeholder(article: Article) -> bool:
    """识别发现页占位条目（仅在无真实内容时兜底展示）"""
    title = (article.title or "").strip()
    summary = (article.summary or "").strip()
    return (
        title.startswith("知乎搜索：")
        or title.startswith("B站搜索：")
        or "未解析到结构化条目" in summary
    )


def _source_key(article: Article) -> str:
    """将发现页来源归一到 zhihu/bilibili/other"""
    src = f"{article.skill_name} {article.source_name}".lower()
    if "知乎" in src or "zhihu" in src:
        return "zhihu"
    if "b站" in src or "bilibili" in src:
        return "bilibili"
    return "other"


def _select_discover_articles(articles: list[Article], count: int) -> list[Article]:
    """
    发现页双源均衡选取策略：

    核心思路：先各自独立从知乎/B站池中选出各自配额，
    再将剩余配额开放给有余量的一方补足，最后补占位兜底。
    这样无论哪方数据多寡，都不会互相通过轮选稀释对方。
    """
    if count <= 0:
        return []

    real_items = [a for a in articles if not _is_discover_placeholder(a)]
    placeholder_items = [a for a in articles if _is_discover_placeholder(a)]

    # 按来源分池，各自按质量分降序
    zhihu_pool = sorted(
        [a for a in real_items if _source_key(a) == "zhihu"],
        key=score_article, reverse=True
    )
    bilibili_pool = sorted(
        [a for a in real_items if _source_key(a) == "bilibili"],
        key=score_article, reverse=True
    )
    other_pool = sorted(
        [a for a in real_items if _source_key(a) == "other"],
        key=score_article, reverse=True
    )

    # 双源各占一半配额（奇数时 B站多一个）
    half = count // 2
    zhihu_quota = half
    bilibili_quota = count - half

    selected: list[Article] = []
    selected_uids: set[str] = set()

    def _pick(pool: list[Article], quota: int) -> list[Article]:
        """从池中按配额选取，记录已选 uid 防重复"""
        result = []
        for a in pool:
            if len(result) >= quota:
                break
            if a.uid not in selected_uids:
                result.append(a)
                selected_uids.add(a.uid)
        return result

    # 第一阶段：双源各取配额
    zhihu_selected = _pick(zhihu_pool, zhihu_quota)
    bilibili_selected = _pick(bilibili_pool, bilibili_quota)
    selected = zhihu_selected + bilibili_selected

    # 第二阶段：将剩余配额开放 -- 先 other，再从双源剩余中取分高者
    remaining = count - len(selected)
    if remaining > 0:
        selected += _pick(other_pool, remaining)
        remaining = count - len(selected)

    if remaining > 0:
        # 知乎或 B站配额未用完（对方数据不足），从剩余候选中补
        zhihu_surplus = [a for a in zhihu_pool if a.uid not in selected_uids]
        bilibili_surplus = [a for a in bilibili_pool if a.uid not in selected_uids]
        surplus = sorted(zhihu_surplus + bilibili_surplus, key=score_article, reverse=True)
        selected += _pick(surplus, remaining)

    # 第三阶段：仍不足则补占位兜底（始终后置）
    remaining = count - len(selected)
    if remaining > 0:
        placeholders = [a for a in placeholder_items if a.uid not in selected_uids]
        placeholders.sort(key=score_article, reverse=True)
        selected += placeholders[:remaining]

    # 发现页最终按质量分排序展示
    selected.sort(key=score_article, reverse=True)
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
        # 并发运行所有 Skill（含超时、缓存、冷却、降级保护）
        tasks = [_run_skill_with_guard(skill, client) for skill in SKILLS]
        results = await asyncio.gather(*tasks)
        mode_counter: dict[str, int] = {}
        for skill_name, articles, mode, _ in results:
            mode_counter[mode] = mode_counter.get(mode, 0) + 1
            logger.debug(f"[采集汇总] {skill_name}: mode={mode}, count={len(articles)}")
            all_articles.extend(articles)
        logger.info(f"Skill 执行模式统计: {mode_counter}")

    logger.info(f"所有 Skill 采集完成，共 {len(all_articles)} 条")
    logger.info(f"[全量刷新] 原始诊断: {_article_diag_summary(all_articles)}")

    raw_articles = list(all_articles)
    all_articles = filter_relevant(all_articles)
    logger.info(f"相关性过滤后 {len(all_articles)} 条")
    logger.info(f"[全量刷新] 过滤后诊断: {_article_diag_summary(all_articles)}")

    relevant_articles = list(all_articles)
    all_articles = deduplicate(all_articles)
    logger.info(f"去重后 {len(all_articles)} 条")
    logger.info(f"[全量刷新] 去重后诊断: {_article_diag_summary(all_articles)}")

    for article in all_articles:
        article.demand_signal_score = _calc_demand_signal_score(article)
        article.is_potential_warehouse_demand = article.demand_signal_score >= 0.8

    national_pool = [a for a in all_articles if (a.region_scope or "national") != "local"]
    local_pool = [
        a for a in all_articles
        if ((a.region_scope or "national") == "local")
        or (
            (a.region_scope or "national") == "wechat"
            and (
                ((a.wechat_category or "industry").strip().lower() == "local")
                or _is_local_whitelist_source(a)
            )
        )
    ]
    discover_pool = [a for a in all_articles if (a.region_scope or "national") == "discover"]
    wechat_pool = [
        a for a in all_articles
        if (a.region_scope or "national") == "wechat"
        and ((a.wechat_category or "industry").strip().lower() != "local")
        and (not _is_local_whitelist_source(a))
    ]
    national_pool = [
        a for a in national_pool
        if (a.region_scope or "national") not in ("discover", "wechat")
    ]

    selected_national = select_national_articles(national_pool, MAX_ARTICLES, per_source_limit=NATIONAL_SOURCE_MAX_COUNT, unknown_date_limit=NATIONAL_UNKNOWN_DATE_MAX_COUNT, max_age_days=MAX_ARTICLE_AGE_DAYS)
    write_diagnostics(raw_articles, relevant_articles, all_articles, selected_national, [s for s in SKILLS if _skill_matches_scope(s, "national")], results, RUNTIME_DIR / "national_news_diagnostics.json")
    selected_local = select_articles(list(local_pool), MAX_ARTICLES_LOCAL)
    selected_discover = _select_discover_articles(list(discover_pool), MAX_ARTICLES_DISCOVER)
    selected_wechat = select_articles(list(wechat_pool), MAX_ARTICLES_WECHAT)
    selected = selected_national + selected_local + selected_discover + selected_wechat
    selected.sort(key=lambda a: a.published_ts, reverse=True)

    # 统一打上“本轮抓取时间”，确保前端手动刷新可在完成后自动重载
    fetched_mark = time.time()
    for article in selected:
        article.fetched_at = fetched_mark

    logger.info(
        "最终精选 "
        f"{len(selected)} 条（国内 {len(selected_national)} / 本地 {len(selected_local)} / 发现 {len(selected_discover)} / 公众号 {len(selected_wechat)}）"
    )
    return selected


def _skill_matches_scope(skill: "NewsSkill", scope: str) -> bool:
    """判断 Skill 是否属于目标范围"""
    region = (getattr(skill, "region_scope", "national") or "national").strip().lower()
    if scope == "discover":
        return region == "discover"
    if scope == "wechat":
        return region == "wechat"
    if scope == "local":
        # 本地页同时需要“本地公众号”数据（region=wechat, wechat_category=local）
        return region in ("local", "wechat")
    # national: 仅国内主资讯，不包含 local/discover/wechat
    return region not in ("local", "discover", "wechat")


def _select_by_scope(articles: list[Article], scope: str) -> list[Article]:
    """从已清洗后的文章中按范围精选"""
    if scope == "discover":
        pool = [a for a in articles if (a.region_scope or "national") == "discover"]
        return _select_discover_articles(pool, MAX_ARTICLES_DISCOVER)
    if scope == "wechat":
        pool = [
            a for a in articles
            if (a.region_scope or "national") == "wechat"
            and ((a.wechat_category or "industry").strip().lower() != "local")
            and (not _is_local_whitelist_source(a))
        ]
        return select_articles(pool, MAX_ARTICLES_WECHAT)
    if scope == "local":
        pool = [
            a for a in articles
            if ((a.region_scope or "national") == "local")
            or (
                (a.region_scope or "national") == "wechat"
                and (
                    ((a.wechat_category or "industry").strip().lower() == "local")
                    or _is_local_whitelist_source(a)
                )
            )
        ]
        rss_local_candidates = [a for a in pool if a.skill_name == "公众号RSS"]
        selected = select_articles(pool, MAX_ARTICLES_LOCAL)
        if selected:
            return selected

        # 本地页兜底：
        # 若最终精选为 0，则直接从“已经进入本地池”的文章中补最近的 2-3 篇。
        # pool 本身已经经过了本地相关性过滤，因此这里不再做第三次判定，
        # 只负责在时效门槛过严时避免页面空白。
        fallback_candidates = list(pool)
        logger.info(
            "[局部刷新/local] 本地精选诊断: "
            f"pool={len(pool)}, "
            f"rss_local={len(rss_local_candidates)}, "
            f"fallback_candidates={len(fallback_candidates)}"
        )
        fallback_candidates.sort(
            key=lambda a: (
                1 if a.skill_name == "本地项目" else 0,
                a.published_ts if a.published_ts > 0 else 0.0,
                score_article(a),
            ),
            reverse=True,
        )
        fallback_selected = fallback_candidates[:3]
        if fallback_selected:
            logger.info(f"[局部刷新/local] 触发兜底补入 {len(fallback_selected)} 条最近本地公众号文章")
        return fallback_selected

    pool = [
        a for a in articles
        if (a.region_scope or "national") not in ("local", "discover", "wechat")
    ]
    return select_national_articles(pool, MAX_ARTICLES, per_source_limit=NATIONAL_SOURCE_MAX_COUNT, unknown_date_limit=NATIONAL_UNKNOWN_DATE_MAX_COUNT, max_age_days=MAX_ARTICLE_AGE_DAYS)


async def fetch_news_by_scope(scope: str) -> list[Article]:
    """
    按 scope 局部抓取资讯（仅运行对应 Skill 集合）
    scope: national / local / discover / wechat
    """
    scope = (scope or "national").strip().lower()
    if scope not in ("national", "local", "discover", "wechat"):
        scope = "national"

    from services.skills import SKILLS  # 延迟导入，避免循环依赖
    target_skills = [s for s in SKILLS if _skill_matches_scope(s, scope)]
    if not target_skills:
        logger.warning(f"[局部刷新] scope={scope} 无可用 Skill")
        return []

    all_articles: list[Article] = []
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        tasks = [_run_skill_with_guard(skill, client) for skill in target_skills]
        results = await asyncio.gather(*tasks)
        mode_counter: dict[str, int] = {}
        for skill_name, articles, mode, _ in results:
            mode_counter[mode] = mode_counter.get(mode, 0) + 1
            logger.debug(f"[局部刷新/{scope}] {skill_name}: mode={mode}, count={len(articles)}")
            logger.info(f"[局部刷新/{scope}] Skill 结果 [{skill_name}]: {_article_diag_summary(articles)}")
            all_articles.extend(articles)
        logger.info(f"[局部刷新/{scope}] Skill 执行模式统计: {mode_counter}")

    logger.info(f"[局部刷新/{scope}] 原始汇总 {len(all_articles)} 条")
    logger.info(f"[局部刷新/{scope}] 原始诊断: {_article_diag_summary(all_articles)}")
    raw_articles = list(all_articles)
    all_articles = filter_relevant(all_articles)
    logger.info(f"[局部刷新/{scope}] 相关性过滤后 {len(all_articles)} 条")
    logger.info(f"[局部刷新/{scope}] 过滤后诊断: {_article_diag_summary(all_articles)}")
    relevant_articles = list(all_articles)
    all_articles = deduplicate(all_articles)
    logger.info(f"[局部刷新/{scope}] 去重后 {len(all_articles)} 条")
    logger.info(f"[局部刷新/{scope}] 去重后诊断: {_article_diag_summary(all_articles)}")

    for article in all_articles:
        article.demand_signal_score = _calc_demand_signal_score(article)
        article.is_potential_warehouse_demand = article.demand_signal_score >= 0.8

    selected = _select_by_scope(all_articles, scope)
    if scope == "national":
        write_diagnostics(raw_articles, relevant_articles, all_articles, selected, target_skills, results, RUNTIME_DIR / "national_news_diagnostics.json")

    # 统一写入本轮抓取时间，前端用于判断是否有新数据
    fetched_mark = time.time()
    for article in selected:
        article.fetched_at = fetched_mark

    logger.info(f"[局部刷新/{scope}] 最终精选 {len(selected)} 条")
    return selected
