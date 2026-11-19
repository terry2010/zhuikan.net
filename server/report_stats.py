#!/usr/bin/env python3
"""每日统计管线：轮转 news-update.jsonl → 聚合 → markdown 报表 → push 私有仓。

cron（VPS，root）:
  0 0 * * * /usr/bin/python3 /data1/htdocs/zhuikan.net/server/report_stats.py >> /tmp/zhuikan-stats.log 2>&1

流程:
  1. mv /data1/log/nginx/news-update.jsonl → news-update.<日期>.jsonl
     + `docker exec nginx nginx -s reopen`（nginx 重开 fd，不写稀疏洞）
  2. 解析 /v1/check 行：args 里的 pkg/version/companions 即上报数据
  3. state.db（报表仓内 .gitignore 的工作状态）：seen(ip, first_day) 追踪首装
  4. 渲染 reports/YYYY-MM-DD.md + 更新 README 索引 → git add/commit/push

幂等：同日重跑覆盖同一报表文件；无新日志行时跳过 commit。
"""
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs

LOG_DIR = "/data1/log/nginx"
LOG_FILE = os.path.join(LOG_DIR, "news-update.jsonl")
NGINX_CONTAINER = "nginx"
STATS_REPO = "/data1/service/zhuikan-stats"  # 私有仓 checkout（deploy key 可写）
KEEP_DAYS = 90  # 轮转出的 .jsonl 保留天数（对齐隐私承诺"留存不超过 90 天"）
# 上报签名 key——与 dsh-rss-news-daily/src/update-check.ts 的 REPORT_KEY 一致。
# 公开源码内：防随手伪造/扫描噪音，不防逆向。
REPORT_KEY = "9f4c2d7a1e5b83f60c4a9d2e7b153f8c6a0d4e9b2f7a5c3d8e1b6f0a4c9d2e7b"


def verify_token(q: dict) -> bool:
    """复算 t=v1.<hmac16>：hmac(v1|iid|d|pkg|version|companions)。"""
    iid, d, t = q.get("iid", [""])[0], q.get("d", [""])[0], q.get("t", [""])[0]
    if not iid or not t.startswith("v1."):
        return False
    canon = "v1|%s|%s|%s|%s|%s" % (
        iid, d, q.get("pkg", [""])[0], q.get("version", [""])[0], q.get("companions", [""])[0])
    sig = hmac.new(REPORT_KEY.encode(), canon.encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(t, f"v1.{sig}")


def run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def rotate(day: str) -> str | None:
    """mv 当前 jsonl 为 .<day>，通知 nginx 重开日志。返回切片路径（空则 None）。"""
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        return None
    sliced = f"{LOG_FILE}.{day}"
    os.replace(LOG_FILE, sliced)
    run("docker", "exec", NGINX_CONTAINER, "nginx", "-s", "reopen")
    return sliced


def parse_reports(path: str) -> list[dict]:
    """解 jsonl；只取 /v1/check 的 GET，args → {pkg,version,companions}。"""
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("uri") not in ("/v1/check", "/latest.json") or rec.get("m") != "GET":
                continue  # /latest.json 兼容旧 conf（try_files 内部重写把 uri 改成了文件名）
            q = parse_qs(rec.get("args", ""))
            pkg, ver = (q.get("pkg", [""])[0], q.get("version", [""])[0])
            if not pkg or not ver:
                continue  # 没带上报字段的探测请求（healthz 式的 curl）不算装机
            comp = {}
            raw = q.get("companions", [""])[0]
            if raw:
                try:
                    comp = {k: str(v) for k, v in json.loads(raw).items()}
                except (json.JSONDecodeError, AttributeError):
                    pass
            iid = q.get("iid", [""])[0]
            verified = verify_token(q)
            # 去重 key：验签通过的 iid 优先（精确到装机），否则回落 /24 截断 IP
            key = f"iid:{iid}" if (iid and verified) else f"ip:{rec.get('ip', '-')}"
            out.append({"ip": rec.get("ip", "-"), "iid": iid, "verified": verified, "key": key,
                        "pkg": pkg, "version": ver, "companions": comp, "t": rec.get("t", "")})
    return out


def update_seen(db: sqlite3.Connection, keys: set[str], day: str) -> int:
    """登记当日独立安装 key（iid: 或 ip: 前缀）；返回其中首次出现的数量。"""
    db.execute("CREATE TABLE IF NOT EXISTS seen (ip TEXT PRIMARY KEY, first_day TEXT)")
    db.execute("UPDATE seen SET ip = 'ip:' || ip WHERE ip NOT LIKE '%:%'")  # 存量裸 IP 一次性迁移
    have = {r[0] for r in db.execute("SELECT ip FROM seen")}
    new = keys - have
    db.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)", [(k, day) for k in new])
    db.commit()
    return len(new)


def render(day: str, reports: list[dict], new_installs: int) -> str:
    keys = {r["key"] for r in reports}
    verified_n = sum(1 for r in reports if r["verified"])
    by_key: dict = {}
    for r in sorted(reports, key=lambda x: x["t"]):
        by_key[r["key"]] = r  # 同装机多次上报取最后一次的装配状态
    uniq = list(by_key.values())

    pv = Counter((r["pkg"], r["version"]) for r in uniq)
    cv = Counter((n, v) for r in uniq for n, v in r["companions"].items())
    cc = Counter(len(r["companions"]) for r in uniq)
    span = ""
    if reports:
        ts = sorted(r["t"] for r in reports if r["t"])
        if ts:
            span = f"（{ts[0][:19]}Z → {ts[-1][:19]}Z）"

    def table(counter: Counter, c1: str, c2: str) -> str:
        if not counter:
            return "_无数据_\n"
        rows = "\n".join(f"| {k[0]} | {k[1]} | {n} |" for k, n in counter.most_common())
        return f"| {c1} | {c2} | 人数 |\n|---|---|---|\n{rows}\n"

    def cc_table() -> str:
        if not cc:
            return "_无数据_\n"
        rows = "\n".join(f"| {n} 个伴随插件 | {c} |" for n, c in sorted(cc.items()))
        return f"| 伴随插件个数 | 人数 |\n|---|---|\n{rows}\n"

    return f"""# {day} 插件活跃日报{span}

- 上报请求数：{len(reports)}（验签通过 {verified_n} · 无/无效签名 {len(reports) - verified_n}）
- 独立安装（iid 验签去重，无签名回落 /24 IP）：**{len(keys)}**
- 第一天使用（新装）：**{new_installs}** · 回访：{len(keys) - new_installs}

## 版本分布（独立安装数）

{table(pv, "pkg", "version")}
## 伴随插件装配（独立安装数）

{table(cv, "companion", "version")}
## 每人装配的伴随插件个数

{cc_table()}"""


def update_index(repo: str, day: str) -> None:
    readme = os.path.join(repo, "README.md")
    head = "# zhuikan-stats\n\n插件更新检查端点的每日聚合报表（自动生成）。\n\n"
    lines = [head]
    if os.path.exists(readme):
        old = open(readme, encoding="utf-8").read()
        if old.startswith(head):
            lines = [head] + [l for l in old[len(head):].splitlines() if l.strip()]
        else:
            lines = [old.rstrip(), ""]
    entry = f"- [{day}](reports/{day}.md)"
    body = [l for l in lines[1:] if l != entry and l.startswith("- [")]
    body.insert(0, entry)
    with open(readme, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(body) + "\n")


def publish(repo: str, day: str) -> None:
    gi = os.path.join(repo, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("state.db\n*.tmp\n")
    run("git", "-C", repo, "add", "-A")
    if not run("git", "-C", repo, "status", "--porcelain"):
        print(f"{day}: no changes, skip push")
        return
    run("git", "-C", repo, "-c", "user.name=zhuikan-stats-bot", "-c", "user.email=bot@zhuikan.net",
        "commit", "-q", "-m", f"report {day}")
    run("git", "-C", repo, "push", "-q")
    print(f"{day}: report pushed")


def main() -> int:
    day = (date.today() - timedelta(days=1)).isoformat()  # 报表/切片按昨日命名（04:30 跑时内容主要是昨天）
    repo = os.environ.get("STATS_REPO", STATS_REPO)
    if not os.path.isdir(os.path.join(repo, ".git")):
        print(f"stats repo missing: {repo}（先 clone 私有仓并配 deploy key）", file=sys.stderr)
        return 1

    sliced = rotate(day)
    if not sliced:
        print(f"{day}: empty log, skip")
        return 0
    reports = parse_reports(sliced)
    db = sqlite3.connect(os.path.join(repo, "state.db"))
    try:
        new_installs = update_seen(db, {r["key"] for r in reports}, day)
    finally:
        db.close()

    os.makedirs(os.path.join(repo, "reports"), exist_ok=True)
    with open(os.path.join(repo, "reports", f"{day}.md"), "w", encoding="utf-8") as f:
        f.write(render(day, reports, new_installs))
    update_index(repo, day)
    publish(repo, day)

    # 清理过期切片（隐私承诺 90 天留存）
    cutoff = datetime.now().timestamp() - KEEP_DAYS * 86400
    for name in os.listdir(LOG_DIR):
        p = os.path.join(LOG_DIR, name)
        if name.startswith("news-update.jsonl.") and os.path.getmtime(p) < cutoff:
            os.remove(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
