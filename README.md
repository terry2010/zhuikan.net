# zhuikan.net 站点

两部分都部署在**同一台 VPS**（nginx in Docker + certbot 证书，贴合现有 /data1 布局）：

```
zhuikan.net/
  public/            # → /data1/htdocs/zhuikan.net（git clone，nginx root 指到其 public/）
    index.html / *.html / style.css / favicon.svg
  server/            # → /data1/service/zhuikan-update（同仓 clone，容器工作目录 /app/server）
    update-server.mjs      零依赖端点（Node >=18），容器内监听 0.0.0.0:8788
    latest.json            ★ 唯一事实源：改这个文件 = 发布新版本公告
    reports.jsonl          （运行时生成）上报记录：ts/ip(截断)/ua/pkg/version/companions
    zhuikan.net.nginx.conf nginx 配置（放 /data1/conf/nginx/conf.d/）
    deploy_check.sh        每 5 分钟 pull 两处 checkout，server/ 变更则重启容器
    zhuikan-update.service systemd 备选（不走容器时用）
```

## 一、DNS（已完成）

`zhuikan.net` / `www` / `news-update` 三条 A 记录 → VPS IP。

## 二、git 拉代码（VPS 上执行）

```bash
git clone https://github.com/terry2010/zhuikan.net.git /data1/htdocs/zhuikan.net
git clone https://github.com/terry2010/zhuikan.net.git /data1/service/zhuikan-update
```

nginx `root` 指到 `/var/www/html/zhuikan.net/public`——server/ 与 .git 不暴露。

## 三、起更新端点容器（webnet 内，容器名即反代目标）

```bash
docker run -d --name zhuikan-update \
  --network=webnet \
  -v /data1/service/zhuikan-update/server:/app -w /app \
  -e UPDATE_HOST=0.0.0.0 -e UPDATE_PORT=8788 \
  --restart=unless-stopped --log-opt max-size=10m --log-opt max-file=3 \
  node:22-alpine node update-server.mjs
```

注意 `UPDATE_HOST=0.0.0.0` 必须显式给——容器内默认 127.0.0.1 nginx 够不到。

## 四、签证书 + 放 nginx 配置（certbot webroot）

```bash
# 80 块里的 /.well-known/acme-challenge/ 指向 /var/www/webroot（=/data1/webroot）
# 先放只有 80 块的 conf（或 default server 已带 acme 路径则直接签）
certbot certonly --webroot -w /data1/webroot \
  -d zhuikan.net -d www.zhuikan.net -d news-update.zhuikan.net

# 证书按现有约定拷到 /data1/certs/zhuikan.net/（见 /etc/letsencrypt/renewal-hooks/）
cp /data1/service/zhuikan-update/server/zhuikan.net.nginx.conf /data1/conf/nginx/conf.d/zhuikan.net.conf
docker exec nginx nginx -t && docker exec nginx nginx -s reload
```

续期已自动：`/etc/cron.d/certbot` 每 12h `certbot -q renew`；确认
`renewal-hooks` 里有「拷到 /data1/certs + reload nginx 容器」的钩子即可。

## 五、自动部署 cron（git pull，仿 zhuikan-api 模式）

```
*/5 * * * * /data1/service/zhuikan-update/server/deploy_check.sh >> /tmp/zhuikan-net-deploy.log 2>&1
```

官网 checkout 每 5 分钟 ff-pull（改完 push 即上线）；service checkout 有变更且
涉及 server/ 时自动 `docker restart zhuikan-update`。

## 六、发新版本公告

编辑 `/data1/service/zhuikan-update/server/latest.json`（每请求重读，免重启）：

```json
{ "latest": "1.3.0", "url": "https://github.com/.../releases/tag/v1.3.0",
  "notes": "一句话公告（阅读器横幅显示，≤200 字符纯文本）",
  "companions": { "dsh-rss-news-zhuikan": { "latest": "0.3.0" } } }
```

官网版本徽标不用管——`/v1/versions` 每小时轮询 npm 自动跟上（daily 以 latest.json 兜底）。

## 七、自检

```bash
curl -I https://zhuikan.net/                       # 官网 200
curl https://news-update.zhuikan.net/healthz       # {"ok":true}
curl https://news-update.zhuikan.net/v1/versions   # 4 个包版本（官网徽标数据源）
curl https://news-update.zhuikan.net/v1/check      # latest.json 内容
curl -X POST https://news-update.zhuikan.net/v1/check \
  -H 'content-type: application/json' \
  -d '{"pkg":"dsh-rss-news-daily","version":"1.2.0","companions":{"dsh-rss-news-zhuikan":"0.2.0"}}'
tail -f /data1/service/zhuikan-update/server/reports.jsonl
```

## 备选：不走容器（host + systemd）

```bash
# host 上 Node >=18 时可用 zhuikan-update.service（已绑 172.18.0.1:8788，
# 只暴露给 webnet 网桥）；nginx conf 里 proxy_pass 改为 http://172.18.0.1:8788
```
