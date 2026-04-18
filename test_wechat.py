"""
微信公众号 Skill 独立测试脚本

用法: python test_wechat.py
仅运行 WeChatSkill，不影响正式数据源配置。
"""
import asyncio
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

import httpx
from config import USER_AGENT, REQUEST_TIMEOUT
from services.skills.wechat import WeChatSkill


async def main():
    print("=" * 50)
    print("  微信公众号 Skill 测试")
    print("=" * 50)

    skill = WeChatSkill()
    print(f"\n关键词组: {len(skill.search_queries)} 个")
    for q in skill.search_queries:
        print(f"  - [{q['label']}] {q['keyword']}")

    print(f"\n开始采集（顺序请求，每组间隔2秒）...\n")

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        articles = await skill.fetch_all(client)

    print("\n" + "=" * 50)
    if not articles:
        print("  未获取到任何文章")
        print("  可能原因: 搜狗反爬拦截 / 网络问题")
    else:
        print(f"  共获取 {len(articles)} 条文章")
        print("=" * 50)
        for i, a in enumerate(articles, 1):
            print(f"\n--- 第 {i} 条 ---")
            print(f"  标题: {a.title}")
            print(f"  来源: {a.source_name or '(无)'}")
            print(f"  时间: {a.published or '(无)'}")
            print(f"  摘要: {a.summary[:80] + '...' if len(a.summary) > 80 else a.summary or '(无)'}")
            print(f"  图片: {'有' if a.image_url else '无'}")
            print(f"  链接: {a.url[:60]}...")


if __name__ == "__main__":
    asyncio.run(main())
