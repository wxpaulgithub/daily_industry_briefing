"""
使用 Playwright 从本机浏览器登录态导出 Cookie，并覆盖 runtime/cookie.txt。

典型用法（先关闭正在运行的 Edge）：
  python scripts/refresh_cookie_file_playwright.py --site zhihu --browser msedge

如需指定用户目录：
  python scripts/refresh_cookie_file_playwright.py --site zhihu --browser msedge --user-data-dir "C:\\Users\\你的用户名\\AppData\\Local\\Microsoft\\Edge\\User Data" --profile-directory Default
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import COOKIE_FILE
from services.cookie_store import load_cookie_map


SITE_URLS = {
    "zhihu": "https://www.zhihu.com/",
    "bilibili": "https://www.bilibili.com/",
}


def _default_user_data_dir(browser: str) -> str:
    local = os.environ.get("LOCALAPPDATA", "")
    if browser == "msedge":
        return str(Path(local) / "Microsoft" / "Edge" / "User Data")
    if browser == "chrome":
        return str(Path(local) / "Google" / "Chrome" / "User Data")
    # chromium 无统一默认目录，要求用户显式传入
    return ""


def _select_sites(site_arg: str) -> list[str]:
    if site_arg == "both":
        return ["zhihu", "bilibili"]
    return [site_arg]


def _cookies_to_header(cookies: list[dict], site: str) -> str:
    domain_mark = "zhihu.com" if site == "zhihu" else "bilibili.com"
    pairs: list[str] = []
    seen: set[str] = set()
    for c in cookies:
        name = str(c.get("name", "")).strip()
        value = str(c.get("value", "")).strip()
        domain = str(c.get("domain", "")).strip()
        if not name or not value:
            continue
        if domain_mark not in domain:
            continue
        if name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _write_cookie_file(cookie_map: Dict[str, str]) -> None:
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "# 站点 Cookie 配置（运行时读取，修改后无需重启）\n"
        "# 由 scripts/refresh_cookie_file_playwright.py 自动更新\n\n"
        "[zhihu]\n"
        f"{cookie_map.get('zhihu', '').strip()}\n\n"
        "[bilibili]\n"
        f"{cookie_map.get('bilibili', '').strip()}\n"
    )
    COOKIE_FILE.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="从本机浏览器登录态导出 Cookie 到 runtime/cookie.txt")
    parser.add_argument("--site", choices=["zhihu", "bilibili", "both"], default="zhihu", help="导出站点")
    parser.add_argument("--browser", choices=["msedge", "chrome", "chromium"], default="msedge", help="浏览器类型")
    parser.add_argument("--user-data-dir", default="", help="浏览器用户数据目录")
    parser.add_argument("--profile-directory", default="Default", help="浏览器配置目录（如 Default / Profile 1）")
    parser.add_argument("--headless", action="store_true", help="无头模式运行（默认有头）")
    args = parser.parse_args()

    user_data_dir = (args.user_data_dir or "").strip() or _default_user_data_dir(args.browser)
    if not user_data_dir:
        raise SystemExit("未提供 --user-data-dir，且无法推断默认用户目录。")
    if not Path(user_data_dir).exists():
        raise SystemExit(f"用户目录不存在：{user_data_dir}")

    sites = _select_sites(args.site)
    cookie_map = load_cookie_map(force=True)

    print(f"准备导出站点：{', '.join(sites)}")
    print(f"浏览器：{args.browser}，用户目录：{user_data_dir}，配置：{args.profile_directory}")

    with sync_playwright() as p:
        kwargs = {
            "user_data_dir": user_data_dir,
            "headless": args.headless,
            "args": [f"--profile-directory={args.profile_directory}"],
        }
        if args.browser in ("msedge", "chrome"):
            kwargs["channel"] = args.browser

        try:
            context = p.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            raise SystemExit(
                "浏览器启动失败。请先关闭同一用户目录下正在运行的浏览器后重试。\n"
                f"错误：{e}"
            )

        try:
            page = context.new_page()
            for site in sites:
                url = SITE_URLS[site]
                print(f"访问 {site}: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1800)
                cookie_str = _cookies_to_header(context.cookies([url]), site)
                if not cookie_str:
                    raise SystemExit(
                        f"{site} 未导出到有效 Cookie。请确认该浏览器配置已登录 {site}，然后重试。"
                    )
                cookie_map[site] = cookie_str
                print(f"{site} Cookie 导出成功，长度={len(cookie_str)}")
        finally:
            context.close()

    _write_cookie_file(cookie_map)
    print(f"已更新：{COOKIE_FILE}")


if __name__ == "__main__":
    main()

