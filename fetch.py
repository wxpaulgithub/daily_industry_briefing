"""
独立采集脚本 - 可由 Windows 任务计划程序定时调用
用法: python fetch.py
"""
import asyncio
import argparse
import sys
import logging
from pathlib import Path

# 确保项目目录在 path 中
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from services.fetcher import fetch_all_news
from services.generator import download_article_images, render_both, save_articles_json
from services.opportunity import fetch_opportunities
from services.opportunity.storage import save_snapshot as save_opportunity_snapshot


async def main(scope: str = "national"):
    if scope == "opportunity":
        print("=" * 40)
        print("  智能仓储商机采集")
        print("=" * 40)
        items = await fetch_opportunities()
        path = save_opportunity_snapshot(items)
        print(f"\n采集完成: {len(items)} 条商机")
        print(f"快照已保存到 {path}")
        return

    print("=" * 40)
    print("  智能仓储每日简讯采集")
    print("=" * 40)

    articles = await fetch_all_news()
    if not articles:
        print("未采集到任何资讯")
        return

    download_article_images(articles)
    render_both(articles)
    save_articles_json(articles)

    img_count = sum(1 for a in articles if a.image_url)
    print(f"\n采集完成: {len(articles)} 条资讯, {img_count} 条有配图")
    print(f"页面已保存到 output/ 目录")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="智能仓储行业情报独立采集")
    parser.add_argument(
        "--scope",
        choices=("national", "opportunity"),
        default="national",
        help="采集范围，默认 national；商机使用 opportunity",
    )
    args = parser.parse_args()
    asyncio.run(main(args.scope))
