#!/usr/bin/env bash
# zhuikan.net 自动部署：单一 checkout /data1/htdocs/zhuikan.net 每 5 分钟 ff-pull
# server/ 有变更则重启端点容器（官网文件 nginx 直接读盘，拉下来即生效）
# cron:  */5 * * * * /data1/htdocs/zhuikan.net/server/deploy_check.sh >> /tmp/zhuikan-net-deploy.log 2>&1
set -u

REPO=/data1/htdocs/zhuikan.net

before=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" pull --ff-only -q
after=$(git -C "$REPO" rev-parse HEAD)

if [ "$before" != "$after" ]; then
  echo "$(date -Is) pulled $before..$after"
  if git -C "$REPO" diff --name-only "$before" "$after" | grep -q '^server/'; then
    echo "$(date -Is) server/ changed → restart zhuikan-update"
    docker restart zhuikan-update
  fi
fi
