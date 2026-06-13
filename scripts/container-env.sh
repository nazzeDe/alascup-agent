#!/usr/bin/env bash
# ============================================================
# 容器镜像共享配置 (被 build-images.sh / export-images.sh 引用)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ---------- 镜像 tag ----------
TAG="${TAG:-latest}"
REGISTRY="${REGISTRY:-}"

# ---------- 基础镜像 ----------
BASE_PYTHON="ghcr.io/loong64/python:3.13.13-trixie"
BASE_NGINX="ghcr.io/loong64/nginx:1.29.8-debian-perl"
BASE_POSTGRES="ghcr.io/loong64/postgres:18-trixie"

# ---------- 应用镜像 ----------
FRONTEND_IMAGE="${REGISTRY}alascup-frontend:${TAG}"
WEB_SERVER_IMAGE="${REGISTRY}alascup-web-server:${TAG}"
TOOL_SERVER_IMAGE="${REGISTRY}alascup-tool-server:${TAG}"

# 镜像名 -> 构建上下文目录 的映射 (供 build-images.sh 使用)
declare -A IMAGE_CONTEXT=(
    ["$TOOL_SERVER_IMAGE"]="$PROJECT_DIR/tool-server"
    ["$WEB_SERVER_IMAGE"]="$PROJECT_DIR/web-server"
    ["$FRONTEND_IMAGE"]="$PROJECT_DIR/frontend"
)

# 所有应用镜像列表 (供 export-images.sh 使用)
ALL_IMAGES=(
    "$FRONTEND_IMAGE"
    "$WEB_SERVER_IMAGE"
    "$TOOL_SERVER_IMAGE"
)

# 构建所需的基础镜像列表
BASE_IMAGES=(
    "$BASE_PYTHON"
    "$BASE_NGINX"
    "$BASE_POSTGRES"
)
