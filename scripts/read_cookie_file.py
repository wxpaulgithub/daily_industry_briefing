"""
读取并展示 runtime/cookie.txt 的站点 Cookie 状态

用法：
  python scripts/read_cookie_file.py
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.cookie_store import load_cookie_map


def _mask_cookie(value: str) -> str:
    v = (value or "").strip()
    if len(v) <= 20:
        return v
    return v[:10] + " ... " + v[-10:]


def main() -> None:
    data = load_cookie_map(force=True)
    sites = ["zhihu", "bilibili"]
    print("Cookie 文件读取结果：")
    for site in sites:
        cookie = data.get(site, "")
        if cookie:
            print(f"- {site}: 已配置，长度={len(cookie)}，样例={_mask_cookie(cookie)}")
        else:
            print(f"- {site}: 未配置")


if __name__ == "__main__":
    main()
