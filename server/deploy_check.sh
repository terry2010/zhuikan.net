#!/usr/bin/env bash
# zhuikan.net 自动部署：单一 checkout /data1/htdocs/zhuikan.net 每 5 分钟 ff-pull
# 官网/端点文件 nginx 直接读盘，拉下来即生效；nginx conf 有变更则同步 + reload
# cron:  */5 * * * * /data1/htdocs/zhuikan.net/server/deploy_check.sh >> /tmp/zhuikan-net-deploy.log 2>&1
set -u

REPO=/data1/htdocs/zhuikan.net
CONF_DST=/data1/conf/nginx/conf.d/zhuikan.net.conf

before=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" pull --ff-only -q
after=$(git -C "$REPO" rev-parse HEAD)

if [ "$before" = "$after" ]; then
  exit 0
fi
echo "$(date -Is) pulled $before..$after"

if git -C "$REPO" diff --name-only "$before" "$after" | grep -q '^server/zhuikan.net.nginx.conf$'; then
  cp "$REPO/server/zhuikan.net.nginx.conf" "$CONF_DST"
  if docker exec nginx nginx -t; then
    docker exec nginx nginx -s reload
    echo "$(date -Is) nginx conf updated + reloaded"
  else
    echo "$(date -Is) nginx -t FAILED, old conf still running; fix and reload manually"
  fi
fi
