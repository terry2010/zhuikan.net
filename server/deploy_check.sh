#!/usr/bin/env bash
# zhuikan.net 自动部署：两处 checkout 各 pull 一次；server/ 有变更则重启端点容器
# cron:  */5 * * * * /data1/service/zhuikan-update/server/deploy_check.sh >> /tmp/zhuikan-net-deploy.log 2>&1
set -u

SITE=/data1/htdocs/zhuikan.net
SVC=/data1/service/zhuikan-update

git -C "$SITE" pull --ff-only -q 2>&1 | grep -v '^$' || true

before=$(git -C "$SVC" rev-parse HEAD)
git -C "$SVC" pull --ff-only -q
after=$(git -C "$SVC" rev-parse HEAD)

if [ "$before" != "$after" ] && git -C "$SVC" diff --name-only "$before" "$after" | grep -q '^server/'; then
  echo "$(date -Is) server/ changed $before..$after → restart zhuikan-update"
  docker restart zhuikan-update
fi
