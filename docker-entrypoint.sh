#!/bin/sh
# 以 root 進來時：把向量索引目錄交給 app 使用者，再降權執行；
# 舊版映像是 root 在寫 /data/chroma，既有的 volume 不先 chown 新版會寫不進去。
set -e

APP_USER="${APP_USER:-app}"
CHROMA_DIR="${CHROMA_PATH:-/data/chroma}"

if [ "$(id -u)" = "0" ]; then
    if [ -d "$CHROMA_DIR" ]; then
        chown -R "$APP_USER:$APP_USER" "$CHROMA_DIR"
    fi
    exec runuser -u "$APP_USER" -- "$@"
fi

exec "$@"
