# zhuikan.net 站点

全部部署在**同一台 VPS**（nginx in Docker + certbot），**零后端进程**——
更新端点是纯静态文件服务，插件上报数据经 nginx access_log 进统计管线：

```
zhuikan.net/
  public/            # 官网（nginx root 指到 checkout 的 public/ 子目录）
    index.html / *.html / style.css / favicon.svg
  server/
    latest.json            ★ 事实源：改它 = 发新版本公告（改完 git push 生效）
    versions.json          （cron 生成，不跟踪入 git）/v1/versions 数据源
    zhuikan.net.nginx.conf nginx 配置（/data1/conf/nginx/conf.d/）
    deploy_check.sh        cron */5min：ff-pull + conf 变更自动 reload nginx
    fetch_versions.py      cron */30min：npm dist-tags → versions.json
    report_stats.py        cron 每日：轮转 jsonl → markdown 报表 → push 私有仓
```

数据管线：

```
插件 GET /v1/check?pkg=..&version=..&companions=..
  → nginx 发静态 latest.json（query 落进 /data1/log/nginx/news-update.jsonl，IP 截 /24）
  → report_stats.py 每日轮转解析 → state.db 首装追踪
  → reports/YYYY-MM-DD.md push → github.com/terry2010/zhuikan-stats（private）
```

## 一、DNS（已完成）

`zhuikan.net` / `www` / `news-update` 三条 A 记录 → VPS IP。

## 二、git 拉代码 + 报表仓

```bash
git clone https://github.com/terry2010/zhuikan.net.git /data1/htdocs/zhuikan.net

# 私有报表仓：VPS 生成 deploy key（只授权 zhuikan-stats 一个仓的写权限）
ssh-keygen -t ed25519 -f /root/.ssh/zhuikan-stats -N ''
cat /root/.ssh/zhuikan-stats.pub   # → GitHub 仓 Settings → Deploy keys → Add（勾 write）
GIT_SSH_COMMAND='ssh -i /root/.ssh/zhuikan-stats -o IdentitiesOnly=yes' \
  git clone git@github.com:terry2010/zhuikan-stats.git /data1/service/zhuikan-stats
# 持久化该 key：/root/.ssh/config 加
#   Host github.com-stats
#     HostName github.com
#     IdentityFile /root/.ssh/zhuikan-stats
# 然后 git -C /data1/service/zhuikan-stats remote set-url origin git@github.com-stats:terry2010/zhuikan-stats.git
```

## 三、签证书 + 放 nginx 配置（certbot webroot）

```bash
certbot certonly --webroot -w /data1/webroot \
  -d zhuikan.net -d www.zhuikan.net -d news-update.zhuikan.net

cp /data1/htdocs/zhuikan.net/server/zhuikan.net.nginx.conf /data1/conf/nginx/conf.d/zhuikan.net.conf
docker exec nginx nginx -t && docker exec nginx nginx -s reload
```

续期全自动：`/etc/cron.d/certbot` 每 12h renew + `sync-certs.sh` deploy hook
拷证书到 `/data1/certs/` 并 reload 容器。

## 四、cron 三条

```
*/5  * * * * /data1/htdocs/zhuikan.net/server/deploy_check.sh >> /tmp/zhuikan-net-deploy.log 2>&1
*/30 * * * * /usr/bin/python3 /data1/htdocs/zhuikan.net/server/fetch_versions.py >> /tmp/zhuikan-versions.log 2>&1
0 0 * * * /usr/bin/python3 /data1/htdocs/zhuikan.net/server/report_stats.py >> /tmp/zhuikan-stats.log 2>&1
```

## 五、发新版本公告

**改仓库里的 `server/latest.json` 然后 push**（不要在 VPS 上直接改——
会弄脏 checkout 导致 ff-pull 卡住）：

```json
{ "latest": "1.3.0", "url": "https://github.com/.../releases/tag/v1.3.0",
  "notes": "一句话公告（阅读器横幅显示，≤200 字符纯文本）",
  "companions": { "dsh-rss-news-zhuikan": { "latest": "0.3.0" } } }
```

nginx 每请求重读文件，deploy_check 拉下来即生效。官网版本徽标不用管——
`fetch_versions.py` 每 30 分钟从 npm 自动刷新 versions.json。

## 六、自检

```bash
curl -I https://zhuikan.net/                        # 官网 200
curl https://news-update.zhuikan.net/healthz        # {"ok":true}
curl https://news-update.zhuikan.net/v1/versions    # 4 个包版本（需先跑过一次 fetch_versions.py）
curl https://news-update.zhuikan.net/v1/check       # latest.json 内容
curl 'https://news-update.zhuikan.net/v1/check?pkg=dsh-rss-news-daily&version=1.2.0&companions=%7B%7D'
tail -1 /data1/log/nginx/news-update.jsonl          # 应看到带 args 的 jsonl 行，ip 是 x.x.x.0
```

看报表：github.com/terry2010/zhuikan-stats 的 `reports/` 目录（每日一个 md，
README 自动索引，git log 即历史）。

## 隐私口径

- 落盘 IP 已由 nginx map 截断至 /24（`x.x.x.0`），原始 IP 不出现在任何持久化文件
- 轮转出的 `news-update.jsonl.*` 切片保留 90 天自动删（report_stats.py 内清理）
- state.db 只存截断 IP + 首见日期，在私有仓目录且被 .gitignore 排除
