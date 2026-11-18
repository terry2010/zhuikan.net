#!/usr/bin/env python3
"""每 30 分钟拉 npm dist-tags → server/versions.json（原子写，官网版本徽标数据源）。

cron:
  */30 * * * * /usr/bin/python3 /data1/htdocs/zhuikan.net/server/fetch_versions.py >> /tmp/zhuikan-versions.log 2>&1

versions.json 不跟踪入 git（.gitignore）——它是生成物，跟踪会堵住 deploy_check 的 ff-pull。
npmjs 主源失败自动回退 npmmirror（国内 VPS 可达性兜底）。
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

PKGS = [
    "dsh-rss-news-daily",
    "dsh-rss-news-zhuikan",
    "dsh-rss-news-flash-kuaixun",
    "dsh-rss-news-browser-fetch",
]
REGISTRIES = ["https://registry.npmjs.org", "https://registry.npmmirror.com"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "versions.json")


def fetch_all(registry: str) -> dict:
    vers = {}
    for pkg in PKGS:
        req = urllib.request.Request(f"{registry}/{pkg}", headers={"User-Agent": "zhuikan-versions/1"})
        with urllib.request.urlopen(req, timeout=10) as res:
            vers[pkg] = json.load(res)["dist-tags"]["latest"]
    return vers


def main() -> int:
    err = None
    for reg in REGISTRIES:
        try:
            data = {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": reg, "versions": fetch_all(reg)}
            break
        except Exception as e:  # noqa: BLE001 —— 单源失败换下一个源
            err = e
            print(f"{reg} failed: {e}", file=sys.stderr)
    else:
        print(f"all registries failed, keep old file: {err}", file=sys.stderr)
        return 1
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)
    print(f"{data['updated']} versions.json updated from {reg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
