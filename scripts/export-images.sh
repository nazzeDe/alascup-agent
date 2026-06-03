#!/usr/bin/env bash
# ============================================================
# 将应用镜像导出为 tar 文件
# 用法: ./export-images.sh
#   导出到 ./docker-images/ 目录
# ============================================================
set -euo pipefail

source "$(dirname "$0")/container-env.sh"

EXPORT_DIR="$PROJECT_DIR/docker-images"
mkdir -p "$EXPORT_DIR"

echo "==> 导出镜像到 $EXPORT_DIR ..."
for img in "${ALL_IMAGES[@]}"; do
    if ! docker image inspect "$img" &>/dev/null; then
        echo "[ERROR] 镜像不存在: $img"
        exit 1
    fi
    fname="${img//[:\/]/_}.tar"
    echo "    导出: $fname"
    docker save -o "$EXPORT_DIR/$fname" "$img"
done
echo "    导出完成"
