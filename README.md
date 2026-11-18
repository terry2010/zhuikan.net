# zhuikan.net 站点

两部分都部署在**同一台 VPS**（nginx in Docker + acme.sh 证书，贴合现有 /data1 布局）：

```
zhuikan.net/
  public/            # → /data1/htdocs/zhuikan.net（nginx file_server 根目录）
    index.html / *.html / style.css / favicon.svg
  server/            # → /data1/service/zhuikan-update（容器工作目录）
    update-server.mjs      零依赖端点（Node >=18），容器内监听 0.0.0.0:8788
    latest.json            ★ 唯一事实源：改这个文件 = 发布新版本公告
    reports.jsonl          （运行时生成）上报记录：ts/ip(截断)/ua/pkg/version/companions
    zhuikan.net.nginx.conf nginx 配置（放 /data1/conf/nginx/conf.d/）
    zhuikan-update.service systemd 备选（不走容器时用，见文末）
```

## 一、DNS（已完成）

`zhuikan.net` / `www` / `news-update` 三条 A 记录 → VPS IP。

## 二、上传文件

```bash
# 本机执行
rsync -av --delete public/ user@your-vps:/data1/htdocs/zhuikan.net/
rsync -av server/   user@your-vps:/data1/service/zhuikan-update/
```

## 三、起更新端点容器（webnet 内，容器名即反代目标）

```bash
docker run -d --name zhuikan-update \
  --network=webnet \
  -v /data1/service/zhuikan-update:/app -w /app \
  -e UPDATE_HOST=0.0.0.0 -e UPDATE_PORT=8788 \
  --restart=unless-stopped --log-opt max-size=10m --log-opt max-file=3 \
  node:22-alpine node update-server.mjs
```

注意 `UPDATE_HOST=0.0.0.0` 必须显式给——容器内默认 127.0.0.1 nginx 够不到。

## 四、签证书 + 放 nginx 配置

```bash
# 若 default server 已带 acme-challenge 路径，可直接签；否则先放一个只有 80 块
# + acme-challenge location 的临时 conf，签完再换正式版
acme.sh --issue -d zhuikan.net -d www.zhuikan.net -d news-update.zhuikan.net -w /data1/webroot

cp /data1/service/zhuikan-update/zhuikan.net.nginx.conf /data1/conf/nginx/conf.d/zhuikan.net.conf
docker exec nginx nginx -t && docker exec nginx nginx -s reload
```

## 五、发新版本公告

编辑 `/data1/service/zhuikan-update/latest.json`（每请求重读，免重启）：

```json
{ "latest": "1.3.0", "url": "https://github.com/.../releases/tag/v1.3.0",
  "notes": "一句话公告（阅读器横幅显示，≤200 字符纯文本）",
  "companions": { "dsh-rss-news-zhuikan": { "latest": "0.3.0" } } }
```

官网版本徽标不用管——`/v1/versions` 每小时轮询 npm 自动跟上（daily 以 latest.json 兜底）。

## 六、自检

```bash
curl -I https://zhuikan.net/                       # 官网 200
curl https://news-update.zhuikan.net/healthz       # {"ok":true}
curl https://news-update.zhuikan.net/v1/versions   # 4 个包版本（官网徽标数据源）
curl https://news-update.zhuikan.net/v1/check      # latest.json 内容
curl -X POST https://news-update.zhuikan.net/v1/check \
  -H 'content-type: application/json' \
  -d '{"pkg":"dsh-rss-news-daily","version":"1.2.0","companions":{"dsh-rss-news-zhuikan":"0.2.0"}}'
tail -f /data1/service/zhuikan-update/reports.jsonl
```

## 备选：不走容器（host + systemd）

```bash
# host 上 Node >=18 时可用 zhuikan-update.service（绑 172.18.0.1:8788 即只暴露给 webnet）
# nginx conf 里 proxy_pass 改为 http://172.18.0.1:8788
```
