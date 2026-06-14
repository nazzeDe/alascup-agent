#!/usr/bin/env bash
# ============================================================
# 构建配置 - 自动检测架构 (x86 / loong64)
# 被 build-images.sh / export-images.sh / run-containers.sh 引用
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ---------- 架构检测 ----------
detect_arch() {
    case "${TARGET_ARCH:-$(uname -m)}" in
        loongarch64|loong64) echo "loong64" ;;
        *)                  echo "x86" ;;
    esac
}

ARCH=$(detect_arch)

# ---------- 镜像 tag ----------
TAG="${TAG:-latest}"
REGISTRY="${REGISTRY:-}"

# ---------- 基础镜像 (x86 默认, 国内用 ghcr.io 镜像源) ----------
BASE_PYTHON="python:3.13"
BASE_NGINX="ghcr.io/nginxinc/nginx-unprivileged:alpine"
BASE_POSTGRES="ghcr.io/cloudnative-pg/postgresql:18"

# ---------- 龙芯覆盖 ----------
if [ "$ARCH" = "loong64" ]; then
    BASE_PYTHON="ghcr.io/loong64/python:3.13.13-trixie"
    BASE_NGINX="ghcr.io/loong64/nginx:1.29.8-debian-perl"
    BASE_POSTGRES="ghcr.io/loong64/postgres:18-trixie"
    PIP_EXTRA_INDEX_URL="https://mirrors.loong64.com/pypi/simple"
fi

# ---------- 各服务对应的基础镜像 (供 build-images.sh 使用) ----------
BASE_IMAGE_FRONTEND="$BASE_NGINX"
BASE_IMAGE_WEB_SERVER="$BASE_PYTHON"
BASE_IMAGE_TOOL_SERVER="$BASE_PYTHON"

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

# 构建所需的基础镜像列表 (龙芯需要预拉取)
BASE_IMAGES=(
    "$BASE_PYTHON"
    "$BASE_NGINX"
    "$BASE_POSTGRES"
)
