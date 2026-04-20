"""
交互式更新 runtime/cookie.txt 中的站点 Cookie。

使用方法：
  python scripts/update_cookie.py --site zhihu
  python scripts/update_cookie.py --site both

脚本会提示你在浏览器 F12 中复制 Cookie，粘贴到终端后自动写入 cookie.txt。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import COOKIE_FILE
from services.cookie_store import load_cookie_map

SITE_INSTRUCTIONS = {
    "zhihu": {
        "url": "https://www.zhihu.com/",
        "api_check": "https://www.zhihu.com/api/v4/me",
        "key_check": "z_c0",
        "steps": (
            "1. 在浏览器中打开 https://www.zhihu.com/ 并确认已登录\n"
            "2. 按 F12 打开开发者工具，切到 Network 标签\n"
            "3. 按 Ctrl+R 刷新页面\n"
            "4. 点击任意 zhihu.com 请求，在 Request Headers 中找到 Cookie:\n"
            "5. 复制冒号后面的完整内容（整行）"
        ),
    },
    "bilibili": {
        "url": "https://www.bilibili.com/",
        "api_check": "https://api.bilibili.com/x/web-interface/nav",
        "key_check": "SESSDATA",
        "steps": (
            "1. 在浏览器中打开 https://www.bilibili.com/ 并确认已登录\n"
            "2. 按 F12 打开开发者工具，切到 Network 标签\n"
            "3. 按 Ctrl+R 刷新页面\n"
            "4. 点击任意 bilibili.com 请求，在 Request Headers 中找到 Cookie:\n"
            "5. 复制冒号后面的完整内容（整行）"
        ),
    },
}


def _select_sites(site_arg: str) -> list[str]:
    if site_arg == "both":
        return ["zhihu", "bilibili"]
    return [site_arg]


def _write_cookie_file(cookie_map: Dict[str, str]) -> None:
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "# 站点 Cookie 配置（运行时读取，修改后无需重启）\n\n"
        "[zhihu]\n"
        f"{cookie_map.get('zhihu', '').strip()}\n\n"
        "[bilibili]\n"
        f"{cookie_map.get('bilibili', '').strip()}\n"
    )
    COOKIE_FILE.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="交互式更新 cookie.txt")
    parser.add_argument("--site", choices=["zhihu", "bilibili", "both"], default="zhihu", help="更新的站点")
    args = parser.parse_args()

    sites = _select_sites(args.site)
    cookie_map = load_cookie_map(force=True)

    print("=" * 60)
    print("  交互式 Cookie 更新工具")
    print("=" * 60)
    print(f"  目标文件: {COOKIE_FILE}")
    print(f"  更新站点: {', '.join(sites)}")
    print("=" * 60)

    for site in sites:
        info = SITE_INSTRUCTIONS[site]
        print(f"\n--- {site.upper()} ---")
        print(f"\n请按以下步骤获取 {site} 的 Cookie:\n")
        print(info["steps"])
        print(f"\n注意: 确保粘贴的 Cookie 中包含 {info['key_check']}，这是关键认证凭证")
        print()

        while True:
            try:
                raw = input(f"请粘贴 {site} Cookie（输入后按回车，输入 skip 跳过）:\n").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已取消。")
                return

            if raw.lower() == "skip":
                print(f"跳过 {site}")
                break

            if not raw:
                print("Cookie 为空，请重新粘贴。")
                continue

            # 检查关键 key 是否存在
            key = info["key_check"]
            if key not in raw:
                print(f"警告: Cookie 中缺少关键凭证 {key}，请确认复制了完整的 Cookie 内容。")
                retry = input("是否仍要保存？(y/n): ").strip().lower()
                if retry != "y":
                    continue

            cookie_map[site] = raw
            print(f"{site} Cookie 已记录，长度={len(raw)}")
            break

    _write_cookie_file(cookie_map)
    print(f"\n已保存到: {COOKIE_FILE}")

    # 验证提示
    print("\n验证方式:")
    for site in sites:
        print(f"  python -c \"import httpx; r=httpx.get('{SITE_INSTRUCTIONS[site]['api_check']}', headers={{'Cookie': open('{COOKIE_FILE}').read().split('[{site}]')[1].split('[')[0].strip()}}); print('{site}:', r.status_code)\"")
    print(f"  或运行: python scripts/read_cookie_file.py")


if __name__ == "__main__":
    main()

